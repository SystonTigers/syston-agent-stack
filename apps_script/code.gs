/**
 * Google Apps Script Listener for your football automation.
 * Receives JSON POST requests and writes to your Sheet.
 */

function doPost(e) {
  try {
    var body = e.postData.contents;
    var data = JSON.parse(body);

    // Open your sheet by ID or name
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheetByName('Live Match Updates'); // change to your tab name

    // Example: append new row with data fields
    sheet.appendRow([
      new Date(),                      // Timestamp
      data.event || '',                // Event type
      data.text || '',                 // Message or score text
      JSON.stringify(data)             // Full JSON for reference
    ]);

    return ContentService.createTextOutput(
      JSON.stringify({status: 'ok', received: data})
    ).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({status: 'error', message: err.toString()})
    ).setMimeType(ContentService.MimeType.JSON);
  }
}
