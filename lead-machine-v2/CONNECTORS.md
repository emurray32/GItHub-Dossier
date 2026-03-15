# Connectors

## Required MCP Server

This plugin requires the Lead Machine MCP server (`mcp_server.py`) which exposes v2 intent-signal-first tools.

**Connection:** Add the Lead Machine MCP server in your Claude project settings.

## V2 MCP Tools Used by This Plugin

### Signal Queue
- `list_signal_queue` — list signals filtered by status/owner
- `get_signal_workspace` — full workspace context for a signal (account, campaign, prospects, drafts)
- `get_signal_counts` — counts by workflow status
- `get_signal_owners` — distinct account owners

### Prospect Discovery
- `find_prospects` — search Apollo for people at a signal's account domain
- `save_prospects` — persist found prospects (with server-side filtering)
- `get_prospects` — get saved prospects for a signal

### Draft Generation
- `generate_draft_sequence` — generate 3-step email sequence for a prospect
- `regenerate_draft_step` — regenerate a draft with critique feedback
- `save_edited_draft` — save manual subject/body edits
- `approve_draft` — mark a draft as approved
- `approve_all_drafts` — approve all drafts for a prospect

### Enrollment
- `enroll_prospect` — enroll a single prospect into Apollo sequence
- `bulk_enroll_prospects` — enroll multiple prospects at once
- `mark_sequence_complete` — mark a prospect's sequence as done

### Account Management
- `mark_account_sequenced` — at least one prospect enrolled
- `mark_account_noise` — false positive, archives all signals
- `mark_account_revisit` — sequences done, flag for follow-up
- `reset_account_status` — undo status back to 'new'

### Campaign & Writing
- `list_campaigns` — all available campaigns
- `recommend_campaign` — auto-recommend campaign for a signal
- `update_signal_campaign` — change a signal's campaign
- `get_writing_preferences` — org-wide writing rules
- `update_writing_preference` — update a writing rule

### Analytics
- `pipeline_analytics` — full funnel conversion metrics
- `campaign_analytics` — per-campaign performance
- `draft_analytics` — draft quality and regeneration metrics
