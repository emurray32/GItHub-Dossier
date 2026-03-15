import { useState, useMemo, useEffect, useCallback, useRef } from "react";

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

// Queue context — injected by command so BDR knows position
const QUEUE_POSITION = 1;   // current position (1-indexed)
const QUEUE_TOTAL = 12;     // total signals in queue

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

function Kbd({ children }) {
  return (
    <kbd className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 text-[10px] font-mono font-semibold bg-slate-100 text-slate-500 border border-slate-300 rounded shadow-sm">
      {children}
    </kbd>
  );
}

// ═══════════════════════════════════════════════════════════════════
// COMPONENTS
// ═══════════════════════════════════════════════════════════════════

function SignalHeader() {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-4 mb-3">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-xl font-bold text-slate-900 truncate">{ACCOUNT.company_name}</h1>
            <Badge status={ACCOUNT.account_status} colorMap={STATUS_COLORS} />
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
              {SIGNAL.signal_type?.replace(/_/g, " ")}
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs text-slate-400">
            {ACCOUNT.website && <a href={ACCOUNT.website} target="_blank" className="hover:text-blue-600">{ACCOUNT.website}</a>}
            {ACCOUNT.industry && <span>{ACCOUNT.industry}</span>}
            {ACCOUNT.company_size && <span>{ACCOUNT.company_size}</span>}
            <span>via {SIGNAL.signal_source}</span>
            <span>{timeAgo(SIGNAL.created_at)}</span>
          </div>
        </div>
        <div className="text-xs text-slate-400 font-mono whitespace-nowrap ml-3">
          {QUEUE_POSITION}/{QUEUE_TOTAL}
        </div>
      </div>
      <p className="text-sm text-slate-600 mt-2 bg-amber-50 rounded px-3 py-2 border border-amber-100">
        {SIGNAL.signal_description}
      </p>
    </div>
  );
}

function CampaignBanner({ selectedId, onSelect }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-3 mb-3">
      <div className="flex items-center gap-3">
        <span className="text-xs font-medium text-slate-500 uppercase tracking-wide">Campaign</span>
        <select
          value={selectedId}
          onChange={(e) => onSelect(Number(e.target.value))}
          className="text-sm border rounded px-2 py-1 flex-1 max-w-xs"
        >
          {ALL_CAMPAIGNS.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} {c.id === CAMPAIGN.id ? "(rec)" : ""}
            </option>
          ))}
        </select>
        {CAMPAIGN.writing_guidelines && (
          <span className="text-xs text-slate-400 truncate flex-1" title={CAMPAIGN.writing_guidelines}>
            {CAMPAIGN.writing_guidelines}
          </span>
        )}
      </div>
    </div>
  );
}

function DraftEditor({ draft, onChange }) {
  if (!draft) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Badge status={draft.status} colorMap={DRAFT_STATUS_COLORS} />
      </div>
      <input
        type="text"
        value={draft.subject}
        onChange={(e) => onChange({ ...draft, subject: e.target.value, status: draft.status === "generated" ? "edited" : draft.status })}
        className="w-full text-sm border rounded px-3 py-1.5 font-medium focus:ring-2 focus:ring-blue-300 focus:border-blue-400 outline-none"
        placeholder="Subject line"
      />
      <textarea
        value={draft.body}
        onChange={(e) => onChange({ ...draft, body: e.target.value, status: draft.status === "generated" ? "edited" : draft.status })}
        className="w-full text-sm border rounded px-3 py-2 font-mono focus:ring-2 focus:ring-blue-300 focus:border-blue-400 outline-none"
        rows={6}
        placeholder="Email body"
      />
    </div>
  );
}

function ProspectCard({ prospect, drafts, included, focused, onToggle, onDraftChange, activeStep, onStepChange }) {
  const activeDraft = drafts?.find((d) => d.step_number === activeStep);

  return (
    <div className={`bg-white rounded-lg shadow-sm border p-4 mb-2 transition-all ${
      focused ? "ring-2 ring-blue-400 border-blue-300" : ""
    } ${!included ? "opacity-50" : ""}`}>
      {/* Prospect header — single row */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={onToggle}
            className={`w-5 h-5 rounded border-2 flex items-center justify-center flex-shrink-0 transition ${
              included
                ? "bg-emerald-500 border-emerald-500 text-white"
                : "bg-white border-slate-300 text-transparent hover:border-slate-400"
            }`}
            title={`${included ? "Exclude" : "Include"} prospect (S)`}
          >
            {included && (
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-slate-900 text-sm">{prospect.full_name}</span>
              <span className="text-xs text-slate-400">{prospect.title}</span>
            </div>
            <div className="flex items-center gap-2 text-xs text-slate-400">
              <span>{prospect.email}</span>
              {prospect.email_verified && <span className="text-emerald-500">verified</span>}
              {prospect.linkedin_url && (
                <a href={prospect.linkedin_url} target="_blank" className="hover:text-blue-500">LI</a>
              )}
            </div>
          </div>
        </div>
        <Badge status={prospect.enrollment_status} colorMap={ENROLLMENT_COLORS} />
      </div>

      {/* Draft tabs + editor — always visible */}
      {included && drafts && drafts.length > 0 && (
        <div>
          <div className="flex gap-1 mb-2 border-b">
            {[1, 2, 3].map((step) => {
              const d = drafts.find((dr) => dr.step_number === step);
              return (
                <button
                  key={step}
                  onClick={() => onStepChange(step)}
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

function ShortcutsOverlay({ onClose }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full mx-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold text-slate-900">Keyboard Shortcuts</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="space-y-2 text-sm">
          {[
            ["j / k", "Next / prev prospect"],
            ["1  2  3", "Switch draft step"],
            ["s", "Toggle include/exclude"],
            ["Enter", "Approve & Enroll all"],
            ["n", "Mark as noise + next"],
            ["Esc", "Close modal / cancel"],
            ["?", "Show this help"],
          ].map(([key, desc]) => (
            <div key={key} className="flex items-center justify-between py-1 border-b border-slate-100 last:border-0">
              <span className="text-slate-600">{desc}</span>
              <div className="flex gap-1">
                {key.split(/\s+/).filter(Boolean).map((k, i) =>
                  k === "/" ? <span key={i} className="text-slate-300 mx-0.5">/</span> : <Kbd key={i}>{k}</Kbd>
                )}
              </div>
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400 mt-4">Shortcuts are disabled when editing a text field.</p>
      </div>
    </div>
  );
}

function ConfirmationPanel({ approvedProspects, campaignName, onCancel, onConfirm }) {
  // Auto-focus confirm button so Enter works immediately
  const confirmRef = useRef(null);
  useEffect(() => { confirmRef.current?.focus(); }, []);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-md w-full mx-4">
        <h3 className="text-lg font-bold text-slate-900 mb-3">Confirm Enrollment</h3>
        <div className="space-y-2 mb-4">
          <p className="text-sm text-slate-600">
            <strong>{approvedProspects.length}</strong> prospect{approvedProspects.length !== 1 ? "s" : ""} into <strong>{campaignName}</strong>:
          </p>
          <ul className="text-sm text-slate-700 list-disc ml-5">
            {approvedProspects.map((p) => (
              <li key={p.id}>{p.full_name} — {p.email}</li>
            ))}
          </ul>
        </div>
        <div className="flex items-center gap-3">
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className="flex-1 px-4 py-2.5 bg-emerald-600 text-white rounded-lg font-medium hover:bg-emerald-700 transition focus:ring-2 focus:ring-emerald-400 focus:outline-none"
          >
            Enroll {approvedProspects.length} <Kbd>Enter</Kbd>
          </button>
          <button
            onClick={onCancel}
            className="px-4 py-2.5 text-slate-500 hover:text-slate-700 text-sm"
          >
            Cancel <span className="text-slate-300 text-xs">Esc</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function NoiseConfirmPanel({ companyName, onCancel, onConfirm }) {
  const confirmRef = useRef(null);
  useEffect(() => { confirmRef.current?.focus(); }, []);

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full mx-4">
        <h3 className="text-lg font-bold text-slate-900 mb-2">Mark as Noise?</h3>
        <p className="text-sm text-slate-600 mb-4">
          This archives <strong>{companyName}</strong> and all its signals.
        </p>
        <div className="flex items-center gap-3">
          <button
            ref={confirmRef}
            onClick={onConfirm}
            className="flex-1 px-4 py-2.5 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 transition focus:ring-2 focus:ring-red-400 focus:outline-none"
          >
            Confirm Noise <Kbd>Enter</Kbd>
          </button>
          <button
            onClick={onCancel}
            className="px-4 py-2.5 text-slate-500 hover:text-slate-700 text-sm"
          >
            Cancel <span className="text-slate-300 text-xs">Esc</span>
          </button>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════════════

export default function BDRReview() {
  const [inclusions, setInclusions] = useState(() => {
    const init = {};
    PROSPECTS.forEach((p) => { init[p.id] = true; });
    return init;
  });
  const [drafts, setDrafts] = useState(DRAFTS);
  const [selectedCampaign, setSelectedCampaign] = useState(CAMPAIGN.id);
  const [focusedIdx, setFocusedIdx] = useState(0);
  const [activeSteps, setActiveSteps] = useState(() => {
    const init = {};
    PROSPECTS.forEach((p) => { init[p.id] = 1; });
    return init;
  });
  const [showConfirm, setShowConfirm] = useState(false);
  const [showNoise, setShowNoise] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [enrollResult, setEnrollResult] = useState(null); // "enrolled" | "noise" | null

  const includedProspects = useMemo(
    () => PROSPECTS.filter((p) => inclusions[p.id]),
    [inclusions]
  );

  const focusedProspect = PROSPECTS[focusedIdx];

  // ─── Keyboard handler ───
  useEffect(() => {
    function handler(e) {
      // Don't capture when typing in inputs
      const tag = e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
        // Only capture Escape from inputs
        if (e.key === "Escape") {
          e.target.blur();
          e.preventDefault();
        }
        return;
      }

      // Modal-specific shortcuts
      if (showConfirm || showNoise) {
        if (e.key === "Escape") {
          setShowConfirm(false);
          setShowNoise(false);
          e.preventDefault();
        }
        // Enter is handled by the focused button in the modal
        return;
      }

      if (showHelp) {
        if (e.key === "Escape" || e.key === "?") {
          setShowHelp(false);
          e.preventDefault();
        }
        return;
      }

      // Prevent if already resolved
      if (enrollResult) return;

      switch (e.key) {
        case "j":
          setFocusedIdx((i) => Math.min(i + 1, PROSPECTS.length - 1));
          e.preventDefault();
          break;
        case "k":
          setFocusedIdx((i) => Math.max(i - 1, 0));
          e.preventDefault();
          break;
        case "1":
        case "2":
        case "3":
          if (focusedProspect) {
            setActiveSteps((prev) => ({ ...prev, [focusedProspect.id]: Number(e.key) }));
          }
          e.preventDefault();
          break;
        case "s":
          if (focusedProspect) {
            setInclusions((prev) => ({ ...prev, [focusedProspect.id]: !prev[focusedProspect.id] }));
          }
          e.preventDefault();
          break;
        case "Enter":
          if (includedProspects.length > 0) {
            setShowConfirm(true);
          }
          e.preventDefault();
          break;
        case "n":
          setShowNoise(true);
          e.preventDefault();
          break;
        case "?":
          setShowHelp(true);
          e.preventDefault();
          break;
        default:
          break;
      }
    }

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [showConfirm, showNoise, showHelp, focusedProspect, includedProspects, enrollResult]);

  // ─── Handlers ───
  const handleToggle = (prospectId) => {
    setInclusions((prev) => ({ ...prev, [prospectId]: !prev[prospectId] }));
  };

  const handleDraftChange = (prospectId, updatedDraft) => {
    setDrafts((prev) => ({
      ...prev,
      [prospectId]: prev[prospectId].map((d) =>
        d.id === updatedDraft.id ? updatedDraft : d
      ),
    }));
  };

  const handleStepChange = (prospectId, step) => {
    setActiveSteps((prev) => ({ ...prev, [prospectId]: step }));
  };

  const handleEnrollConfirm = () => {
    setShowConfirm(false);
    setEnrollResult("enrolled");
  };

  const handleNoiseConfirm = () => {
    setShowNoise(false);
    setEnrollResult("noise");
  };

  // ─── Post-action state ───
  if (enrollResult === "enrolled") {
    return (
      <div className="max-w-3xl mx-auto p-4">
        <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-8 text-center">
          <div className="text-4xl mb-3">&#10003;</div>
          <h2 className="text-xl font-bold text-emerald-800 mb-2">
            {includedProspects.length} prospect{includedProspects.length !== 1 ? "s" : ""} enrolled
          </h2>
          <p className="text-sm text-emerald-600 mb-1">{ACCOUNT.company_name} — {ALL_CAMPAIGNS.find(c => c.id === selectedCampaign)?.name}</p>
          <div className="text-sm text-slate-500 mt-4 space-y-1">
            {includedProspects.map(p => (
              <div key={p.id}>{p.full_name} ({p.email})</div>
            ))}
          </div>
          <p className="text-xs text-slate-400 mt-6">
            Confirm in chat to proceed. Say <strong>"enroll"</strong> or move to the next signal.
          </p>
        </div>
      </div>
    );
  }

  if (enrollResult === "noise") {
    return (
      <div className="max-w-3xl mx-auto p-4">
        <div className="bg-slate-50 border border-slate-200 rounded-xl p-8 text-center">
          <div className="text-4xl mb-3 text-slate-400">&#10005;</div>
          <h2 className="text-xl font-bold text-slate-700 mb-2">
            {ACCOUNT.company_name} marked as noise
          </h2>
          <p className="text-sm text-slate-500">All signals for this account have been archived.</p>
          <p className="text-xs text-slate-400 mt-6">
            Confirm in chat. Say <strong>"noise"</strong> or move to the next signal.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto p-4 pb-20">
      <SignalHeader />
      <CampaignBanner selectedId={selectedCampaign} onSelect={setSelectedCampaign} />

      {/* Prospect section header */}
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-medium text-slate-500 uppercase tracking-wide">
          Prospects ({PROSPECTS.length}) — {includedProspects.length} selected
        </h3>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <Kbd>j</Kbd><Kbd>k</Kbd> navigate
          <Kbd>s</Kbd> toggle
          <Kbd>?</Kbd> help
        </div>
      </div>

      {/* All prospects visible — no carousel */}
      {PROSPECTS.map((p, idx) => (
        <ProspectCard
          key={p.id}
          prospect={p}
          drafts={drafts[p.id] || []}
          included={inclusions[p.id]}
          focused={idx === focusedIdx}
          onToggle={() => handleToggle(p.id)}
          onDraftChange={handleDraftChange}
          activeStep={activeSteps[p.id] || 1}
          onStepChange={(step) => handleStepChange(p.id, step)}
        />
      ))}

      {/* Sticky Action Bar */}
      <div className="fixed bottom-0 left-0 right-0 bg-white/95 backdrop-blur border-t shadow-lg p-3 z-40">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <button
            onClick={() => includedProspects.length > 0 && setShowConfirm(true)}
            disabled={includedProspects.length === 0}
            className="px-5 py-2 bg-emerald-600 text-white rounded-lg font-medium hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition flex items-center gap-2"
          >
            Enroll {includedProspects.length} <Kbd>Enter</Kbd>
          </button>
          <div className="flex items-center gap-4 text-xs text-slate-400">
            <span className="font-mono">{QUEUE_POSITION} of {QUEUE_TOTAL}</span>
          </div>
          <button
            onClick={() => setShowNoise(true)}
            className="px-4 py-2 bg-red-50 text-red-600 rounded-lg text-sm font-medium hover:bg-red-100 transition flex items-center gap-2"
          >
            Noise <Kbd>n</Kbd>
          </button>
        </div>
      </div>

      {/* Modals */}
      {showConfirm && (
        <ConfirmationPanel
          approvedProspects={includedProspects}
          campaignName={ALL_CAMPAIGNS.find((c) => c.id === selectedCampaign)?.name || "Unknown"}
          onCancel={() => setShowConfirm(false)}
          onConfirm={handleEnrollConfirm}
        />
      )}

      {showNoise && (
        <NoiseConfirmPanel
          companyName={ACCOUNT.company_name}
          onCancel={() => setShowNoise(false)}
          onConfirm={handleNoiseConfirm}
        />
      )}

      {showHelp && (
        <ShortcutsOverlay onClose={() => setShowHelp(false)} />
      )}
    </div>
  );
}
