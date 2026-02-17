--
name: apollo-prospecting
description: Expert strategies for prospecting and building targeted lead lists using Apollo.io's People and Organization Search APIs. Covers ICP targeting, filtering, technology-based prospecting, and signal-based outreach.
---

# Apollo.io Prospecting Skill

You are an expert at building targeted prospect lists using Apollo.io's search and enrichment APIs. This skill covers how to find ideal customers, build account lists, identify decision makers, and prepare prospects for outreach sequences.

## 1. Prospecting Philosophy

Apollo's database contains 210M+ contacts and 35M+ companies. The key to effective prospecting is NOT pulling massive lists. Use precise filters to find the right people at the right companies at the right time. Quality over quantity.

## 2. Building Your ICP with Apollo Filters

### Company-Level Filters (Organization Search)
Use `POST /api/v1/mixed_companies/search`:

**By Size:** `organization_num_employees_ranges[]` - Use strings like "1,10" (startups), "50,200" (mid-market), "1000,5000" (enterprise).

**By Location:** `organization_locations[]` for HQ targeting. `organization_not_locations[]` to exclude territories.

**By Revenue:** `revenue_range[min]` / `revenue_range[max]` - integers, no currency symbols.

**By Technology Stack:** `currently_using_any_of_technology_uids[]` - 1,500+ supported technologies. Use underscores for spaces (e.g., "google_analytics", "salesforce", "react").

**By Funding:** `latest_funding_amount_range`, `latest_funding_date_range`, `total_funding_range` - find recently funded companies with budget.

**By Hiring Signals:** `q_organization_job_titles[]` - companies hiring for specific roles signal investment in that area.

**By Industry:** `q_organization_keyword_tags[]` - keywords like "saas", "fintech", "healthcare".

### Person-Level Filters (People API Search)
Use `POST /api/v1/mixed_people/api_search`:

**By Title:** `person_titles[]` - partial matches included. Use `include_similar_titles=false` for exact.

**By Seniority:** `person_seniorities[]` - options: owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern.

**By Location:** `person_locations[]` (where they live) vs `organization_locations[]` (company HQ).

**By Email Status:** `contact_email_status[]` - "verified" for deliverability, "likely_to_engage" for engagement.

## 3. Prospecting Strategies

### Strategy A: Technology-Based Prospecting
For developer tools, SaaS integrations, platform replacements:
1. Find orgs using competitor tech: `currently_using_any_of_technology_uids[]`
2. 2. Exclude orgs using your product: `currently_not_using_any_of_technology_uids[]`
   3. 3. Filter by company size matching your pricing tier
      4. 4. Find decision makers by title and seniority
        
         5. ### Strategy B: Funding Signal Prospecting
         6. For selling to growing startups:
         7. 1. Search orgs with `latest_funding_date_range` in last 6 months
            2. 2. Filter by funding amount matching your price point
               3. 3. Target VP+ decision makers
                 
                  4. ### Strategy C: Hiring Signal Prospecting
                  5. For selling to teams building a function:
                  6. 1. `q_organization_job_titles[]` for roles related to your product
                     2. 2. Target the hiring manager or VP leading that function
                       
                        3. ### Strategy D: Account-Based Prospecting
                        4. For enterprise targeting named accounts:
                        5. 1. `q_organization_domains_list[]` with up to 1,000 target domains
                           2. 2. Find all contacts matching your persona at those accounts
                              3. 3. Build multi-threaded outreach (multiple contacts per account)
                                
                                 4. ### Strategy E: GitHub Dossier + Apollo Pipeline (Core Use Case)
                                 5. 1. GitHub Dossier scans company GitHub for i18n/localization signals
                                    2. 2. Use domain for Apollo Org Enrichment: `GET /organizations/enrich?domain={domain}`
                                       3. 3. Get company size, revenue, tech stack, funding data
                                          4. 4. Find people: `POST /mixed_people/api_search` with domain and target titles
                                             5. 5. Enrich best matches: `POST /people/match` for verified emails
                                                6. 6. Create contacts: `POST /contacts` with run_dedupe=true
                                                   7. 7. Add to sequence: `POST /emailer_campaigns/{id}/add_contact_ids`
                                                     
                                                      8. ## 4. Enrichment Best Practices
                                                     
                                                      9. ### Maximizing Match Rates
                                                      10. Provide as much info as possible when enriching:
                                                      11. - **Best**: email address (highest match rate)
                                                          - - **Good**: first_name + last_name + domain
                                                            - - **Good**: linkedin_url
                                                              - - **Okay**: name + organization_name
                                                                - - **Poor**: name alone (too ambiguous)
                                                                 
                                                                  - ### Bulk vs Single
                                                                  - - Single `POST /people/match` for real-time enrichment
                                                                    - - Bulk `POST /people/bulk_match` (up to 10) for batch processing
                                                                      - - Bulk is 50% of single per-minute rate limit
                                                                       
                                                                        - ### Credit Management
                                                                        - - **Free**: People API Search (`/mixed_people/api_search`)
                                                                          - - **Credits**: People Enrichment, Org Search, Org Enrichment, News Search
                                                                            - - **Extra credits**: reveal_personal_emails, reveal_phone_number, waterfall
                                                                              - - Monitor: `POST /usage`
                                                                               
                                                                                - ## 5. The Contact Creation Pipeline
                                                                               
                                                                                - Apollo distinguishes "people" (global DB) from "contacts" (your DB). Only contacts can join sequences.
                                                                               
                                                                                - 1. Search people -> get Apollo person IDs
                                                                                  2. 2. Enrich people -> get emails, titles, company data
                                                                                     3. 3. Create contacts -> `POST /contacts` with run_dedupe=true
                                                                                        4. 4. Add to sequence -> `POST /emailer_campaigns/{id}/add_contact_ids`
                                                                                          
                                                                                           5. ### Before Adding to Sequences
                                                                                           6. - Get sequence ID: `POST /emailer_campaigns/search`
                                                                                              - - Get email account ID: `GET /email_accounts`
                                                                                                - - Handle skipped contacts: contacts_without_email, contacts_unverified_email, contacts_active_in_other_campaigns
                                                                                                 
                                                                                                  - ## 6. Pagination for Large Lists
                                                                                                 
                                                                                                  - Max 100 per page, 500 pages (50K total). If hitting limits:
                                                                                                  - 1. Add more filters (location, seniority, size)
                                                                                                    2. 2. Run multiple narrower searches
                                                                                                       3. 3. Process each page before fetching next
                                                                                                         
                                                                                                          4. ## 7. Domain Formatting (Critical)
                                                                                                         
                                                                                                          5. - CORRECT: "apollo.io", "microsoft.com"
                                                                                                             - - WRONG: "www.apollo.io", "https://apollo.io", "@apollo.io"
                                                                                                               - - Always strip protocols, www, and @ symbols
