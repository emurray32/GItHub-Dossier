--
name: apollo-sequences
description: Expert guide for managing Apollo.io email sequences (campaigns), creating contacts, adding them to sequences, tracking performance, and automating multi-step outreach via API.
---

# Apollo.io Sequences & Campaign Automation Skill

You are an expert at automating sales outreach sequences using Apollo.io's API. This skill covers the complete workflow from creating contacts to managing automated email campaigns, tracking engagement, and handling the deal pipeline.

## 1. Key Apollo Terminology

- **Person**: Someone in Apollo's global database (210M+). Can search/enrich but cannot email.
- - **Contact**: A person added to YOUR team's database. Only contacts can join sequences.
  - - **Account**: A company added to YOUR team's database.
    - - **Sequence** (Emailer Campaign): Automated multi-step email series. API name: `emailer_campaigns`.
      - - **Deal** (Opportunity): Tracked sales opportunity. API name: `opportunities`.
        - - **Task**: Scheduled action item (call, email, LinkedIn) for a team member.
          - - **Label**: Tag/list to organize contacts. Can bulk-add contacts to sequences.
           
            - ## 2. The Complete Sequence Automation Pipeline
           
            - ### Step 1: Get Prerequisites
            - You need three IDs before automating:
           
            - **User ID:** `GET /api/v1/users/search` - returns all team members with IDs.
           
            - **Email Account ID:** `GET /api/v1/email_accounts` - returns linked inboxes. Required for sequence enrollment.
           
            - **Sequence ID:** `POST /api/v1/emailer_campaigns/search` with `q_name` parameter to find sequences by name.
           
            - ### Step 2: Create Contacts
            - People from the global DB must become contacts first:
            - ```
              POST /api/v1/contacts
              Body: {
                "first_name": "Tim",
                "last_name": "Zheng",
                "email": "tim@apollo.io",
                "title": "CEO",
                "organization_name": "Apollo",
                "run_dedupe": true
              }
              ```
              CRITICAL: Always set `run_dedupe: true` to prevent duplicates.

              ### Step 3: Add Contacts to Sequence
              ```
              POST /api/v1/emailer_campaigns/{sequence_id}/add_contact_ids
              Body: {
                "emailer_campaign_id": "{sequence_id}",
                "contact_ids": ["contact_id_1", "contact_id_2"],
                "send_email_from_email_account_id": "{email_account_id}",
                "user_id": "{your_user_id}"
              }
              ```

              ### Step 4: Monitor Status
              Search contacts to check sequence status:
              ```
              POST /api/v1/contacts/search
              Body: { "q_keywords": "tim zheng" }
              ```
              Response includes `contact_campaign_statuses` with status: active, paused, finished, or failed.

              ## 3. Sequence Safety Flags

              | Flag | Default | Purpose |
              |------|---------|---------|
              | `sequence_no_email` | false | Allow contacts without email |
              | `sequence_unverified_email` | false | Allow unverified emails |
              | `sequence_job_change` | false | Allow recent job changers |
              | `sequence_active_in_other_campaigns` | false | Allow contacts in other sequences |
              | `sequence_finished_in_other_campaigns` | false | Allow contacts finished elsewhere |
              | `sequence_same_company_in_same_campaign` | false | Allow multiple contacts from same company |

              **Conservative** (cold outreach): All false. Only verified emails, not in other sequences.
              **Aggressive** (volume): Set unverified, active_in_other, finished_in_other to true.

              ## 4. Handling Skipped Contacts

              Response includes `skipped_contact_ids` hash with reasons:
              - `contact_not_found`: Bad ID
              - - `contacts_already_exists_in_current_campaign`: Already enrolled
                - - `contacts_active_in_other_campaigns`: Override with flag
                  - - `contacts_without_email`: Enrich first
                    - - `contacts_unverified_email`: Override with flag or re-enrich
                      - - `contacts_with_job_change`: Stale data
                        - - `contacts_in_same_company`: Override with flag
                         
                          - ## 5. Scheduled Enrollment
                         
                          - Add contacts as "paused" with auto-unpause:
                          - ```
                            {
                              "status": "paused",
                              "auto_unpause_at": "2025-03-01T09:00:00Z"
                            }
                            ```
                            Useful for coordinating with launches, staggering outreach, or time-zone targeting.

                            ## 6. Label-Based Bulk Enrollment

                            Add all contacts with specific labels:
                            ```
                            {
                              "label_names": ["Q1 Target List", "Enterprise Prospects"],
                              "send_email_from_email_account_id": "{email_account_id}"
                            }
                            ```

                            ## 7. Sequence Performance Metrics

                            Each sequence returns: unique_scheduled, unique_delivered, unique_bounced, unique_opened, unique_replied, unique_clicked, unique_unsubscribed, bounce_rate, open_rate, click_rate, reply_rate, spam_block_rate, opt_out_rate, demo_rate.

                            ## 8. Task Creation for Follow-up

                            ```
                            POST /api/v1/tasks
                            Body: {
                              "user_id": "{user_id}",
                              "contact_id": "{contact_id}",
                              "type": "call",
                              "priority": "high",
                              "status": "scheduled",
                              "due_at": "2025-03-15T14:00:00Z",
                              "note": "Opened 3 emails, no reply. Try direct call."
                            }
                            ```
                            Types: call, outreach_manual_email, linkedin_step_connect, linkedin_step_message, linkedin_step_view_profile, linkedin_step_interact_post, action_item.

                            ## 9. Deal Tracking

                            ```
                            POST /api/v1/opportunities
                            Body: {
                              "name": "Acme Corp - Localization Deal",
                              "owner_id": "{user_id}",
                              "account_id": "{account_id}",
                              "amount": "50000",
                              "closed_date": "2025-06-30"
                            }
                            ```
                            Amount: string, no currency symbols or commas.

                            ## 10. Full GitHub Dossier + Apollo Flow

                            1. GitHub Dossier finds i18n signals at "acme.com"
                            2. 2. Enrich org: `GET /organizations/enrich?domain=acme.com`
                               3. 3. Find people: `POST /mixed_people/api_search` with domain + target titles/seniorities
                                  4. 4. Enrich matches: `POST /people/match` for verified emails
                                     5. 5. Create contacts: `POST /contacts` with run_dedupe=true
                                        6. 6. Create account: `POST /accounts`
                                           7. 7. Find sequence: `POST /emailer_campaigns/search`
                                              8. 8. Get email account: `GET /email_accounts`
                                                 9. 9. Add to sequence: `POST /emailer_campaigns/{id}/add_contact_ids`
                                                    10. 10. Create tasks: `POST /tasks` for high-priority follow-ups
                                                        11. 11. Create deal: `POST /opportunities` to track pipeline
                                                           
                                                            12. ## 11. API Key Requirements
                                                           
                                                            13. All sequence, contact, account, deal, task, and user endpoints require a **Master API key**. Enrichment endpoints work with standard keys. Recommendation: always use Master key.
