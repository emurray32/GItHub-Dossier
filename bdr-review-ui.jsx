import { useState, useMemo } from "react";

// ═══════════════════════════════════════════════════════════════════
// DATA INJECTION — Replace these constants with actual workspace data
// from get_signal_workspace MCP tool
// ═══════════════════════════════════════════════════════════════════

const SIGNAL = {
  id: 42,
  signal_type: "global_expansion",
  signal_description: "Dublin EMEA HQ 125->200+ employees. 750+ EMEA customers. Docs & UI English-only.",
  evidence_value: "Dublin EMEA HQ 125->200+ employees. 750+ EMEA customers. Docs & UI English-only.",
  signal_source: "excel_upload",
  status: "new",
  created_at: "2026-03-13T10:30:00Z",
};

const ACCOUNT = {
  id: 15,
  company_name: "Gong",
  website: "https://gong.io",
  industry: "Sales Intelligence",
  company_size: "1,000+",
  account_status: "new",
};

const CAMPAIGN = {
  id: 3,
  name: "Global Expansion",
  writing_guidelines: "Reference specific market expansion evidence. Lead with the EMEA/APAC angle.",
  reasoning: "Signal type 'global_expansion' matches expansion campaign keywords",
};

const PROSPECTS = [
  {
    id: 101,
    full_name: "Jane Smith",
    first_name: "Jane",
    last_name: "Smith",
    email: "jane@gong.io",
    email_verified: true,
    title: "VP Engineering",
    linkedin_url: "https://linkedin.com/in/janesmith",
    enrollment_status: "found",
  },
  {
    id: 102,
    full_name: "Tom Chen",
    first_name: "Tom",
    last_name: "Chen",
    email: "tom.chen@gong.io",
    email_verified: true,
    title: "Head of Product",
    linkedin_url: "https://linkedin.com/in/tomchen",
    enrollment_status: "found",
  },
];

const DRAFTS = {
  101: [
    { id: 201, step_number: 1, subject: "EMEA expansion at {{company}}", body: "Hey {{first_name}},\n\nNoticed your Dublin office grew to 200+ people but your docs and product UI are still English-only for 750+ European customers.\n\nWe help teams automate localization so your product speaks every market's language without slowing engineering.\n\nWorth a look?\n\n{{sender_first_name}}", status: "generated" },
    { id: 202, step_number: 2, subject: "Following up — {{company}} localization", body: "Hey {{first_name}},\n\nQuick follow-up. Teams scaling into EMEA usually hit a wall when docs and UI stay English-only — support tickets spike and adoption stalls.\n\nPhrase plugs into your CI/CD so translations ship with every release.\n\nOpen to a quick look?\n\n{{sender_first_name}}", status: "generated" },
    { id: 203, step_number: 3, subject: "Last note — {{company}}", body: "Hey {{first_name}},\n\nI'll keep this short. If localizing your product for EMEA customers is on the roadmap, happy to show how we automate the dev-to-translator handoff.\n\nIf not, no worries at all.\n\n{{sender_first_name}}", status: "generated" },
  ],
  102: [
    { id: 204, step_number: 1, subject: "{{company}} product in EMEA", body: "Hey {{first_name}},\n\nSaw that Gong's EMEA presence is growing fast — 750+ customers, Dublin HQ expanding.\n\nCurious if product localization is on your roadmap. We help product teams ship in multiple languages without the manual file juggling.\n\nWorth a quick look?\n\n{{sender_first_name}}", status: "generated" },
    { id: 205, step_number: 2, subject: "Localization at {{company}}", body: "Hey {{first_name}},\n\nFollowing up — when product goes multi-market, translation usually becomes a bottleneck between product and release.\n\nPhrase automates that handoff so your team ships localized releases as fast as English ones.\n\nOpen to seeing how?\n\n{{sender_first_name}}", status: "generated" },
    { id: 206, step_number: 3, subject: "Last note — {{company}}", body: "Hey {{first_name}},\n\nLast note from me. If multi-language product is on the horizon for Gong, happy to share how we fit into your stack.\n\nEither way, best of luck with the EMEA expansion.\n\n{{sender_first_name}}", status: "generated" },
  ],
};

const ALL_CAMPAIGNS = [
  { id: 1, name: "Dependency Detected" },
  { id: 2, name: "Ghost Branch" },
  { id: 3, name: "Global Expansion" },
  { id: 4, name: "Learning Platform" },
];

// ═══════════════════════════════════════════════════════════════════
// HELPERS
// ═══════════════════════════════════════════════════════════════════

const STATUS_COLORS = {
  new: { bg: "bg-blue-100", text: "text-blue-800" },
  sequenced: { bg: "bg-emerald-100", text: "text-emerald-800" },
  revisit: { bg: "bg-amber-100", text: "text-amber-800" },
  noise: { bg: "bg-gray-100", text: "text-gray-500" },
};

const DRAFT_STATUS_COLORS = {
  generated: { bg: "bg-slate-100", text: "text-slate-600" },
  edited: { bg: "bg-blue-100", text: "text-blue-700" },
  approved: { bg: "bg-emerald-100", text: "text-emerald-700" },
  enrolled: { bg: "bg-purple-100", text: "text-purple-700" },
};

const ENROLLMENT_COLORS = {
  found: { bg: "bg-slate-100", text: "text-slate-600" },
  drafting: { bg: "bg-blue-100", text: "text-blue-700" },
  enrolled: { bg: "bg-emerald-100", text: "text-emerald-700" },
  sequence_complete: { bg: "bg-purple-100", text: "text-purple-700" },
};

function timeAgo(dateStr) {
  const now = new Date();
  const then = new Date(dateStr);
  const diffMs = now - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  if (days < 30) return `${Math.floor(days / 7)}w ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function Badge({ status, colorMap }) {
  const colors = colorMap[status] || { bg: "bg-gray-100", text: "text-gray-600" };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${colors.bg} ${colors.text}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ═══════════════════════════════════════════════════════════════════
// COMPONENTS
// ═══════════════════════════════════════════════════════════════════

function SignalHeader() {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-6 mb-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-2xl font-bold text-slate-900">{ACCOUNT.company_name}</h1>
            <Badge status={ACCOUNT.account_status} colorMap={STATUS_COLORS} />
          </div>
          <div className="flex items-center gap-4 text-sm text-slate-500 mb-3">
            {ACCOUNT.website && <a href={ACCOUNT.website} target="_blank" className="hover:text-blue-600">{ACCOUNT.website}</a>}
            {ACCOUNT.industry && <span>{ACCOUNT.industry}</span>}
            {ACCOUNT.company_size && <span>{ACCOUNT.company_size} employees</span>}
          </div>
        </div>
        <span className="text-xs text-slate-400">{timeAgo(SIGNAL.created_at)}</span>
      </div>

      <div className="mt-3 p-3 bg-amber-50 rounded-lg border border-amber-200">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-amber-600 font-medium text-sm">
            {SIGNAL.signal_type?.replace(/_/g, " ") || "Signal"}
          </span>
          <span className="text-xs text-slate-400">via {SIGNAL.signal_source}</span>
        </div>
        <p className="text-sm text-slate-700">{SIGNAL.signal_description}</p>
      </div>
    </div>
  );
}

function CampaignBanner({ selectedId, onSelect }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-4 mb-4">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-medium text-slate-700">Campaign</h3>
        <select
          value={selectedId}
          onChange={(e) => onSelect(Number(e.target.value))}
          className="text-sm border rounded px-2 py-1"
        >
          {ALL_CAMPAIGNS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} {c.id === CAMPAIGN.id ? "(Recommended)" : ""}
            </option>
          ))}
        </select>
      </div>
      {CAMPAIGN.reasoning && (
        <p className="text-xs text-slate-500">{CAMPAIGN.reasoning}</p>
      )}
      {CAMPAIGN.writing_guidelines && (
        <details className="mt-2">
          <summary className="text-xs text-slate-400 cursor-pointer hover:text-slate-600">
            Writing guidelines
          </summary>
          <p className="mt-1 text-xs text-slate-500 bg-slate-50 p-2 rounded">
            {CAMPAIGN.writing_guidelines}
          </p>
        </details>
      )}
    </div>
  );
}

function DraftEditor({ draft, onChange }) {
  if (!draft) return null;
  const colors = DRAFT_STATUS_COLORS[draft.status] || DRAFT_STATUS_COLORS.generated;
  return (
    <div className="mt-2 space-y-2">
      <div className="flex items-center gap-2">
        <Badge status={draft.status} colorMap={DRAFT_STATUS_COLORS} />
      </div>
      <input
        type="text"
        value={draft.subject}
        onChange={(e) => onChange({ ...draft, subject: e.target.value, status: draft.status === "generated" ? "edited" : draft.status })}
        className="w-full text-sm border rounded px-3 py-1.5 font-medium"
        placeholder="Subject line"
      />
      <textarea
        value={draft.body}
        onChange={(e) => onChange({ ...draft, body: e.target.value, status: draft.status === "generated" ? "edited" : draft.status })}
        className="w-full text-sm border rounded px-3 py-2 font-mono"
        rows={8}
        placeholder="Email body"
      />
    </div>
  );
}

function ProspectCard({ prospect, drafts, included, onToggle, onDraftChange }) {
  const [activeStep, setActiveStep] = useState(1);
  const activeDraft = drafts?.find((d) => d.step_number === activeStep);
  const enrollColors = ENROLLMENT_COLORS[prospect.enrollment_status] || ENROLLMENT_COLORS.found;

  return (
    <div className={`bg-white rounded-lg shadow-sm border p-4 mb-3 ${included ? "ring-2 ring-emerald-300" : ""}`}>
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <h4 className="font-semibold text-slate-900">{prospect.full_name}</h4>
            <Badge status={prospect.enrollment_status} colorMap={ENROLLMENT_COLORS} />
          </div>
          <p className="text-sm text-slate-500">{prospect.title}</p>
          <div className="flex items-center gap-3 mt-1 text-xs text-slate-400">
            <span>{prospect.email}</span>
            {prospect.email_verified && <span className="text-emerald-500">verified</span>}
            {prospect.linkedin_url && (
              <a href={prospect.linkedin_url} target="_blank" className="hover:text-blue-500">LinkedIn</a>
            )}
          </div>
        </div>
        <button
          onClick={onToggle}
          className={`px-3 py-1 rounded-full text-sm font-medium transition ${
            included
              ? "bg-emerald-100 text-emerald-700 hover:bg-emerald-200"
              : "bg-slate-100 text-slate-500 hover:bg-slate-200"
          }`}
        >
          {included ? "Include" : "Skip"}
        </button>
      </div>

      {drafts && drafts.length > 0 && (
        <div>
          <div className="flex gap-1 mb-2 border-b">
            {[1, 2, 3].map((step) => {
              const d = drafts.find((dr) => dr.step_number === step);
              return (
                <button
                  key={step}
                  onClick={() => setActiveStep(step)}
                  className={`px-3 py-1.5 text-xs font-medium border-b-2 transition ${
                    activeStep === step
                      ? "border-blue-500 text-blue-600"
                      : "border-transparent text-slate-400 hover:text-slate-600"
                  }`}
                >
                  Step {step}
                  {d && d.status !== "generated" && (
                    <span className="ml-1 w-1.5 h-1.5 rounded-full inline-block bg-blue-500" />
                  )}
                </button>
              );
            })}
          </div>
          <DraftEditor
            draft={activeDraft}
            onChange={(updated) => onDraftChange(prospect.id, updated)}
          />
        </div>
      )}
    </div>
  );
}

function ConfirmationPanel({ approvedProspects, campaignName, onCancel }) {
  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
        <h3 className="text-lg font-bold text-slate-900 mb-3">Confirm Enrollment</h3>
        <div className="space-y-2 mb-4">
          <p className="text-sm text-slate-600">
            <strong>{approvedProspects.length}</strong> prospect{approvedProspects.length !== 1 ? "s" : ""} will be enrolled into <strong>{campaignName}</strong>:
          </p>
          <ul className="text-sm text-slate-700 list-disc ml-5">
            {approvedProspects.map((p) => (
              <li key={p.id}>{p.full_name} ({p.email})</li>
            ))}
          </ul>
        </div>
        <div className="bg-amber-50 border border-amber-200 rounded p-3 mb-4">
          <p className="text-sm text-amber-800">
            Type <strong>"yes, enroll them"</strong> in chat to proceed.
          </p>
        </div>
        <button onClick={onCancel} className="text-sm text-slate-500 hover:text-slate-700">
          Cancel
        </button>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════════════

export default function BDRReview() {
  const [approvals, setApprovals] = useState(() => {
    const init = {};
    PROSPECTS.forEach((p) => { init[p.id] = true; });
    return init;
  });
  const [drafts, setDrafts] = useState(DRAFTS);
  const [selectedCampaign, setSelectedCampaign] = useState(CAMPAIGN.id);
  const [showConfirm, setShowConfirm] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const currentProspect = PROSPECTS[currentIndex];

  const approvedProspects = useMemo(
    () => PROSPECTS.filter((p) => approvals[p.id]),
    [approvals]
  );

  const handleToggle = (prospectId) => {
    setApprovals((prev) => ({ ...prev, [prospectId]: !prev[prospectId] }));
  };

  const handleDraftChange = (prospectId, updatedDraft) => {
    setDrafts((prev) => ({
      ...prev,
      [prospectId]: prev[prospectId].map((d) =>
        d.id === updatedDraft.id ? updatedDraft : d
      ),
    }));
  };

  return (
    <div className="max-w-3xl mx-auto p-4 pb-24">
      <SignalHeader />
      <CampaignBanner selectedId={selectedCampaign} onSelect={setSelectedCampaign} />

      <div className="flex items-center justify-between mb-3">
        <h3 className="font-medium text-slate-700">
          Prospect {currentIndex + 1} of {PROSPECTS.length}
        </h3>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setCurrentIndex((i) => i - 1)}
            disabled={currentIndex === 0}
            className="px-3 py-1 text-sm border rounded-lg hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition"
          >
            Prev
          </button>
          <button
            onClick={() => setCurrentIndex((i) => i + 1)}
            disabled={currentIndex === PROSPECTS.length - 1}
            className="px-3 py-1 text-sm border rounded-lg hover:bg-slate-50 disabled:opacity-30 disabled:cursor-not-allowed transition"
          >
            Next
          </button>
        </div>
      </div>

      <ProspectCard
        key={currentProspect.id}
        prospect={currentProspect}
        drafts={drafts[currentProspect.id] || []}
        included={approvals[currentProspect.id]}
        onToggle={() => handleToggle(currentProspect.id)}
        onDraftChange={handleDraftChange}
      />

      {/* Sticky Action Bar */}
      <div className="fixed bottom-0 left-0 right-0 bg-white border-t shadow-lg p-4">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <button
            onClick={() => setShowConfirm(true)}
            disabled={approvedProspects.length === 0}
            className="px-6 py-2 bg-emerald-600 text-white rounded-lg font-medium hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            Enroll Selected ({approvedProspects.length})
          </button>
          <button className="px-4 py-2 bg-red-50 text-red-600 rounded-lg text-sm font-medium hover:bg-red-100 transition">
            Mark as Noise
          </button>
        </div>
      </div>

      {showConfirm && (
        <ConfirmationPanel
          approvedProspects={approvedProspects}
          campaignName={ALL_CAMPAIGNS.find((c) => c.id === selectedCampaign)?.name || "Unknown"}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}
