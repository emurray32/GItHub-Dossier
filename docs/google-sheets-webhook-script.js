/**
 * RepoRadar → Google Sheets Webhook Integration
 *
 * SETUP INSTRUCTIONS:
 * 1. Open your Google Sheet: https://docs.google.com/spreadsheets/d/1oPnoRB-9fjLEzN1bNdU32xljXVpEuvUJSa6KBquNxCM
 * 2. Go to Extensions → Apps Script
 * 3. Delete any existing code and paste this entire script
 * 4. Click "Deploy" → "New deployment"
 * 5. Select type: "Web app"
 * 6. Set "Execute as": "Me"
 * 7. Set "Who has access": "Anyone"
 * 8. Click "Deploy" and authorize when prompted
 * 9. Copy the Web App URL (https://script.google.com/macros/s/your-script-id/exec)
 * 10. In RepoRadar Settings, paste this URL as your Google Sheets Webhook URL
 *
 * SHEET REQUIREMENTS:
 * - Create a sheet named "Tier 1 & 2 Accounts" (or update SHEET_NAME below)
 * - Add these headers in Row 1:
 *   A: Company | B: Annual Revenue | C: GitHub Org | D: Tier | E: Status |
 *   F: Last Scanned | G: Evidence Summary | H: Report Link | I: Notes |
 *   J: Added Date | K: Updated Date
 */

// =============================================================================
// CONFIGURATION - Customize these settings
// =============================================================================

const CONFIG = {
  // Name of the sheet tab to write data to
  SHEET_NAME: "Tier 1 & 2 Accounts",

  // Column where company names are stored (for deduplication)
  COMPANY_COLUMN: 1, // Column A

  // Whether to update existing rows or skip duplicates
  UPDATE_DUPLICATES: true,

  // Your RepoRadar base URL for report links
  REPORADAR_BASE_URL: "https://your-reporadar-instance.com",

  // Enable logging for debugging
  DEBUG_MODE: true,

  // Secret token for webhook validation (optional - set in RepoRadar settings too)
  WEBHOOK_SECRET: "",

  // Tier filter - only process these tiers (empty array = all tiers)
  ALLOWED_TIERS: [1, 2]
};

// =============================================================================
// TIER CONFIGURATION - Matches RepoRadar tier system
// =============================================================================

const TIER_CONFIG = {
  0: { name: "Tracking", status: "Cold", color: "#9E9E9E", emoji: "👀" },
  1: { name: "Thinking", status: "Warm Lead", color: "#FFC107", emoji: "🔍" },
  2: { name: "Preparing", status: "Hot Lead", color: "#4CAF50", emoji: "🎯" },
  3: { name: "Launched", status: "Too Late", color: "#F44336", emoji: "❌" },
  4: { name: "Not Found", status: "Disqualified", color: "#616161", emoji: "⚠️" }
};

// =============================================================================
// MAIN WEBHOOK HANDLER
// =============================================================================

/**
 * Handles incoming POST requests from RepoRadar webhook
 * @param {Object} e - The event object from the HTTP request
 * @returns {TextOutput} Response to send back to RepoRadar
 */
function doPost(e) {
  const startTime = new Date();

  try {
    // Parse the incoming data
    if (!e || !e.postData || !e.postData.contents) {
      return createResponse(400, "No data received");
    }

    const payload = JSON.parse(e.postData.contents);
    logDebug("Received webhook payload", payload);

    // Validate webhook secret if configured
    if (CONFIG.WEBHOOK_SECRET && payload.secret !== CONFIG.WEBHOOK_SECRET) {
      logDebug("Invalid webhook secret");
      return createResponse(401, "Invalid webhook secret");
    }

    // Extract company data (handle both direct and nested formats)
    const companyData = payload.company || payload.data || payload;

    // Validate required fields
    if (!companyData.company_name) {
      return createResponse(400, "Missing required field: company_name");
    }

    // Check tier filter
    const tier = parseInt(companyData.current_tier) || 0;
    if (CONFIG.ALLOWED_TIERS.length > 0 && !CONFIG.ALLOWED_TIERS.includes(tier)) {
      logDebug(`Skipping tier ${tier} - not in allowed tiers`);
      return createResponse(200, `Skipped: Tier ${tier} not in allowed list`);
    }

    // Process the account
    const result = processAccount(companyData, payload.event_type);

    const duration = new Date() - startTime;
    logDebug(`Webhook processed in ${duration}ms`, result);

    return createResponse(200, result.message, {
      action: result.action,
      row: result.row,
      duration_ms: duration
    });

  } catch (error) {
    logDebug("Error processing webhook", { error: error.toString(), stack: error.stack });
    return createResponse(500, `Error: ${error.message}`);
  }
}

/**
 * Handles GET requests (for testing the webhook endpoint)
 * @param {Object} e - The event object
 * @returns {TextOutput} Status response
 */
function doGet(e) {
  return createResponse(200, "RepoRadar webhook endpoint is active", {
    sheet_name: CONFIG.SHEET_NAME,
    allowed_tiers: CONFIG.ALLOWED_TIERS,
    update_duplicates: CONFIG.UPDATE_DUPLICATES
  });
}

// =============================================================================
// CORE PROCESSING FUNCTIONS
// =============================================================================

/**
 * Processes an account and adds/updates it in the sheet
 * @param {Object} data - The company data from the webhook
 * @param {string} eventType - The type of event (tier_change, new_account, etc.)
 * @returns {Object} Result object with action taken and row number
 */
function processAccount(data, eventType) {
  const sheet = getOrCreateSheet();
  const companyName = data.company_name.trim();

  // Check for existing entry
  const existingRow = findCompanyRow(sheet, companyName);

  // Prepare row data
  const rowData = formatRowData(data, eventType);

  if (existingRow > 0) {
    if (CONFIG.UPDATE_DUPLICATES) {
      // Update existing row
      updateRow(sheet, existingRow, rowData);
      return { action: "updated", row: existingRow, message: `Updated existing entry for ${companyName}` };
    } else {
      // Skip duplicate
      return { action: "skipped", row: existingRow, message: `Skipped duplicate: ${companyName}` };
    }
  } else {
    // Add new row
    const newRow = appendRow(sheet, rowData);
    applyRowFormatting(sheet, newRow, data.current_tier);
    return { action: "added", row: newRow, message: `Added new entry for ${companyName}` };
  }
}

/**
 * Formats the incoming data into a row array matching the sheet columns
 * @param {Object} data - The company data
 * @param {string} eventType - The event type
 * @returns {Array} Array of values for each column
 */
function formatRowData(data, eventType) {
  const tier = parseInt(data.current_tier) || 0;
  const tierInfo = TIER_CONFIG[tier] || TIER_CONFIG[0];

  // Format last scanned date
  let lastScanned = "";
  if (data.last_scanned_at) {
    try {
      lastScanned = new Date(data.last_scanned_at).toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      });
    } catch (e) {
      lastScanned = data.last_scanned_at;
    }
  }

  // Build report link
  let reportLink = "";
  if (data.report_id || data.latest_report_id) {
    const reportId = data.report_id || data.latest_report_id;
    reportLink = `${CONFIG.REPORADAR_BASE_URL}/report/${reportId}`;
  } else if (data.report_link) {
    reportLink = data.report_link;
  }

  // Format revenue for display
  const revenue = formatRevenue(data.annual_revenue);

  // Current timestamp
  const now = new Date();

  return [
    data.company_name,                              // A: Company
    revenue,                                        // B: Annual Revenue
    data.github_org || "",                          // C: GitHub Org
    `${tierInfo.emoji} Tier ${tier}: ${tierInfo.name}`, // D: Tier
    tierInfo.status,                                // E: Status
    lastScanned,                                    // F: Last Scanned
    truncateText(data.evidence_summary || "", 500), // G: Evidence Summary
    reportLink,                                     // H: Report Link
    data.notes || "",                               // I: Notes
    now,                                            // J: Added Date (only set on insert)
    now                                             // K: Updated Date
  ];
}

/**
 * Formats revenue value for consistent display
 * @param {string|number} revenue - Raw revenue value
 * @returns {string} Formatted revenue string
 */
function formatRevenue(revenue) {
  if (!revenue) return "";

  // If already formatted (contains $, M, B, K), return as-is
  if (typeof revenue === "string" && /[\$MBK]/.test(revenue)) {
    return revenue;
  }

  // Try to parse as number and format
  const num = parseFloat(String(revenue).replace(/[^\d.]/g, ""));
  if (isNaN(num)) return String(revenue);

  if (num >= 1e9) {
    return `$${(num / 1e9).toFixed(1)}B`;
  } else if (num >= 1e6) {
    return `$${(num / 1e6).toFixed(0)}M`;
  } else if (num >= 1e3) {
    return `$${(num / 1e3).toFixed(0)}K`;
  }

  return `$${num.toLocaleString()}`;
}

/**
 * Truncates text to a maximum length with ellipsis
 * @param {string} text - Text to truncate
 * @param {number} maxLength - Maximum length
 * @returns {string} Truncated text
 */
function truncateText(text, maxLength) {
  if (!text || text.length <= maxLength) return text || "";
  return text.substring(0, maxLength - 3) + "...";
}

// =============================================================================
// SHEET OPERATIONS
// =============================================================================

/**
 * Gets the target sheet, creating it with headers if it doesn't exist
 * @returns {Sheet} The Google Sheet object
 */
function getOrCreateSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(CONFIG.SHEET_NAME);

  if (!sheet) {
    // Create new sheet with headers
    sheet = ss.insertSheet(CONFIG.SHEET_NAME);

    const headers = [
      "Company",
      "Annual Revenue",
      "GitHub Org",
      "Tier",
      "Status",
      "Last Scanned",
      "Evidence Summary",
      "Report Link",
      "Notes",
      "Added Date",
      "Updated Date"
    ];

    // Set headers
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);

    // Format header row
    const headerRange = sheet.getRange(1, 1, 1, headers.length);
    headerRange.setFontWeight("bold");
    headerRange.setBackground("#1a73e8");
    headerRange.setFontColor("#ffffff");
    headerRange.setHorizontalAlignment("center");

    // Set column widths
    sheet.setColumnWidth(1, 180);  // Company
    sheet.setColumnWidth(2, 120);  // Revenue
    sheet.setColumnWidth(3, 150);  // GitHub Org
    sheet.setColumnWidth(4, 150);  // Tier
    sheet.setColumnWidth(5, 100);  // Status
    sheet.setColumnWidth(6, 150);  // Last Scanned
    sheet.setColumnWidth(7, 350);  // Evidence Summary
    sheet.setColumnWidth(8, 200);  // Report Link
    sheet.setColumnWidth(9, 150);  // Notes
    sheet.setColumnWidth(10, 130); // Added Date
    sheet.setColumnWidth(11, 130); // Updated Date

    // Freeze header row
    sheet.setFrozenRows(1);

    logDebug("Created new sheet with headers", { name: CONFIG.SHEET_NAME });
  }

  return sheet;
}

/**
 * Finds the row number of an existing company entry
 * @param {Sheet} sheet - The sheet to search
 * @param {string} companyName - Company name to find
 * @returns {number} Row number (1-indexed) or 0 if not found
 */
function findCompanyRow(sheet, companyName) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) return 0; // Only header row exists

  const companyColumn = sheet.getRange(2, CONFIG.COMPANY_COLUMN, lastRow - 1, 1).getValues();
  const normalizedSearch = companyName.toLowerCase().trim();

  for (let i = 0; i < companyColumn.length; i++) {
    if (String(companyColumn[i][0]).toLowerCase().trim() === normalizedSearch) {
      return i + 2; // +2 because array is 0-indexed and we start from row 2
    }
  }

  return 0;
}

/**
 * Appends a new row to the sheet
 * @param {Sheet} sheet - The sheet
 * @param {Array} rowData - Data for the new row
 * @returns {number} The row number of the new row
 */
function appendRow(sheet, rowData) {
  sheet.appendRow(rowData);
  return sheet.getLastRow();
}

/**
 * Updates an existing row with new data
 * @param {Sheet} sheet - The sheet
 * @param {number} rowNum - Row number to update
 * @param {Array} rowData - New data for the row
 */
function updateRow(sheet, rowNum, rowData) {
  // Preserve the original "Added Date" (column J, index 9)
  const originalAddedDate = sheet.getRange(rowNum, 10).getValue();
  rowData[9] = originalAddedDate || rowData[9];

  // Update the row
  sheet.getRange(rowNum, 1, 1, rowData.length).setValues([rowData]);

  // Reapply formatting
  const tier = parseInt(String(rowData[3]).match(/Tier (\d)/)?.[1]) || 0;
  applyRowFormatting(sheet, rowNum, tier);
}

/**
 * Applies conditional formatting based on tier
 * @param {Sheet} sheet - The sheet
 * @param {number} rowNum - Row number
 * @param {number} tier - Tier number (0-4)
 */
function applyRowFormatting(sheet, rowNum, tier) {
  const tierInfo = TIER_CONFIG[tier] || TIER_CONFIG[0];
  const numColumns = 11;

  const range = sheet.getRange(rowNum, 1, 1, numColumns);

  // Light background based on tier
  const bgColors = {
    0: "#f5f5f5",  // Grey - Tracking
    1: "#fff8e1",  // Light yellow - Thinking
    2: "#e8f5e9",  // Light green - Preparing
    3: "#ffebee",  // Light red - Launched
    4: "#eceff1"   // Light grey - Not Found
  };

  range.setBackground(bgColors[tier] || bgColors[0]);

  // Make Tier column stand out
  const tierCell = sheet.getRange(rowNum, 4);
  tierCell.setFontWeight("bold");

  // Make Status column colored
  const statusCell = sheet.getRange(rowNum, 5);
  statusCell.setFontColor(tierInfo.color);
  statusCell.setFontWeight("bold");

  // Make Report Link clickable
  const linkCell = sheet.getRange(rowNum, 8);
  const linkValue = linkCell.getValue();
  if (linkValue && linkValue.startsWith("http")) {
    linkCell.setFontColor("#1a73e8");
  }

  // Format GitHub Org as link
  const githubCell = sheet.getRange(rowNum, 3);
  const githubOrg = githubCell.getValue();
  if (githubOrg) {
    githubCell.setFormula(`=HYPERLINK("https://github.com/${githubOrg}", "${githubOrg}")`);
  }
}

// =============================================================================
// UTILITY FUNCTIONS
// =============================================================================

/**
 * Creates a JSON response
 * @param {number} status - HTTP status code
 * @param {string} message - Response message
 * @param {Object} data - Additional data to include
 * @returns {TextOutput} The response object
 */
function createResponse(status, message, data = {}) {
  const response = {
    status: status,
    message: message,
    timestamp: new Date().toISOString(),
    ...data
  };

  return ContentService
    .createTextOutput(JSON.stringify(response))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * Logs debug information if debug mode is enabled
 * @param {string} message - Log message
 * @param {Object} data - Data to log
 */
function logDebug(message, data = null) {
  if (!CONFIG.DEBUG_MODE) return;

  const logMessage = data
    ? `[RepoRadar Webhook] ${message}: ${JSON.stringify(data)}`
    : `[RepoRadar Webhook] ${message}`;

  console.log(logMessage);

  // Also log to a "Webhook Logs" sheet for persistent debugging
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    let logSheet = ss.getSheetByName("Webhook Logs");

    if (!logSheet) {
      logSheet = ss.insertSheet("Webhook Logs");
      logSheet.getRange(1, 1, 1, 3).setValues([["Timestamp", "Message", "Data"]]);
      logSheet.getRange(1, 1, 1, 3).setFontWeight("bold");
    }

    logSheet.appendRow([
      new Date(),
      message,
      data ? JSON.stringify(data) : ""
    ]);

    // Keep only last 1000 log entries
    const lastRow = logSheet.getLastRow();
    if (lastRow > 1001) {
      logSheet.deleteRows(2, lastRow - 1001);
    }
  } catch (e) {
    // Ignore logging errors
  }
}

// =============================================================================
// MANUAL TESTING FUNCTIONS
// =============================================================================

/**
 * Test function - simulates receiving a webhook
 * Run this from the Apps Script editor to test your setup
 */
function testWebhook() {
  const testPayload = {
    event_type: "tier_change",
    timestamp: new Date().toISOString(),
    company: {
      company_name: "Test Company Inc",
      github_org: "test-company",
      annual_revenue: "$50M",
      current_tier: 2,
      last_scanned_at: new Date().toISOString(),
      evidence_summary: "Found i18n library installed. Detected locale configuration files. Active internationalization development.",
      notes: "High priority prospect",
      report_id: 12345
    }
  };

  // Simulate the webhook call
  const mockEvent = {
    postData: {
      contents: JSON.stringify(testPayload)
    }
  };

  const result = doPost(mockEvent);
  console.log("Test result:", result.getContent());
}

/**
 * Clears all data except headers (useful for testing)
 * Run manually from Apps Script editor
 */
function clearAllData() {
  const sheet = getOrCreateSheet();
  const lastRow = sheet.getLastRow();

  if (lastRow > 1) {
    sheet.deleteRows(2, lastRow - 1);
    console.log(`Cleared ${lastRow - 1} rows`);
  }
}

/**
 * Exports current sheet data as JSON (for backup/migration)
 * Run manually from Apps Script editor
 */
function exportAsJson() {
  const sheet = getOrCreateSheet();
  const data = sheet.getDataRange().getValues();
  const headers = data[0];

  const jsonData = data.slice(1).map(row => {
    const obj = {};
    headers.forEach((header, i) => {
      obj[header] = row[i];
    });
    return obj;
  });

  console.log(JSON.stringify(jsonData, null, 2));
  return jsonData;
}
