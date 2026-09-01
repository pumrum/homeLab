/**
 * ============================================================================
 * LEDGER <-> GOOGLE DRIVE INVOICE RECONCILIATION
 * ============================================================================
 *
 * Reads a ledger sheet, compares columns I (filename) and J (URL) against
 * the actual contents of the Drive folders configured on a recon sheet,
 * and writes a discrepancy report back to that recon sheet.
 *
 * SETUP
 *   The ledger and recon tab names are NOT hardcoded in this file. Set them
 *   once per spreadsheet under the Apps Script editor's Project Settings >
 *   Script Properties:
 *     LEDGER_SHEET_NAME   e.g. "Household Ledger"
 *     RECON_SHEET_NAME    e.g. "Reconciliation"
 *
 * SHEETS / RANGES
 *   Ledger sheet (LEDGER_SHEET_NAME)   header row 1, data from row 2
 *     A = Seq, C = Transaction Date, F = Account, I = Invoice (filename),
 *     J = URL
 *
 *   Recon sheet (RECON_SHEET_NAME)
 *     Config table   headers GQ5:GU5 (Inv_Active, Inv_Account, Inv_Year,
 *                    Inv_Folder, Inv_FolderURL), data from GQ6. Only rows
 *                    with Inv_Active = "Y" are checked against Drive.
 *
 *                    Inv_Year is normally a 4-digit year, or "All" for an
 *                    account whose invoices all live in one folder
 *                    regardless of year.
 *
 *                    A config row can also describe a STATEMENTS folder:
 *                    give Inv_Account a "-Statements" suffix (e.g.
 *                    "Credit-Statements") and put the literal filename
 *                    segment that identifies that account's statements in
 *                    Inv_Year (e.g. "Acme_1234" for an account ending in
 *                    1234 at Acme Bank). No bank name is hardcoded anywhere in
 *                    this script -- the set of recognized bank tokens is
 *                    read entirely from these config rows. A ledger row is
 *                    treated as a statement reference whenever its filename
 *                    matches YYYY-MM-DD_<that literal> for some literal
 *                    belonging to the row's own Account; if the filename
 *                    matches a known bank token's shape but not any literal
 *                    configured for that particular account, it's flagged
 *                    STATEMENT_ACCOUNT_MISMATCH rather than treated as an
 *                    ordinary missing invoice. Statement identity (existence
 *                    of the config row) is independent of Inv_Active --
 *                    deactivating a statement scope only stops that scope's
 *                    Drive check, it doesn't change what a filename IS.
 *
 *     Summary block  headers GY5:GZ5 (Metric, Count), data from GY6
 *     Detail table   headers HD5:HJ5 (Seq, Account, Year, Type, Filename,
 *                    URL, Detail), data from HD6
 *
 * ENTRY POINT
 *   runInvoiceRecon()  -- call this from the script editor, a menu, or a
 *                         trigger. Scope is controlled entirely from the
 *                         sheet via the Inv_Active column on the config
 *                         table -- set a row to "Y" to include that
 *                         Account/Year in the run, "N" to skip it.
 * ============================================================================
 */

// ---- Tunable constants -----------------------------------------------------

// Ledger and Recon tab names are read from Script Properties rather than
// hardcoded here, so this script can be shared publicly without exposing
// the names of your actual sheet tabs, and so forking it just means setting
// two properties rather than editing source. Set these under Project
// Settings > Script Properties in the Apps Script editor:
//   LEDGER_SHEET_NAME  -- name of your ledger tab (e.g. "Household Ledger")
//   RECON_SHEET_NAME   -- name of your recon tab (e.g. "Reconciliation")
// See loadSheetNames_() below -- it throws a clear error if either is unset.

var LEDGER_HEADER_ROW = 1;
var LEDGER_DATA_START_ROW = 2;

var LEDGER_COL = {
  SEQ: 1,      // A
  DATE: 3,     // C
  ACCOUNT: 6,  // F
  FILENAME: 9, // I
  URL: 10      // J
};

var CONFIG_HEADER_ROW = 5;
var CONFIG_DATA_START_ROW = 6;
var CONFIG_START_COL = 199; // GQ = column 199

var SUMMARY_HEADER_ROW = 5;
var SUMMARY_DATA_START_ROW = 6;
var SUMMARY_START_COL = 207; // GY = column 207
var SUMMARY_ROWS_TO_CLEAR = 20; // generous, only ~13 used

var DETAIL_HEADER_ROW = 5;
var DETAIL_DATA_START_ROW = 6;
var DETAIL_START_COL = 212; // HD = column 212
var DETAIL_COLS = 7;
var DETAIL_ROWS_TO_CLEAR = 2000; // generous headroom for a few thousand ledger rows

var FILENAME_SENTINELS = ['zNo_Invoice', 'zMissing_Invoice'];
var URL_SENTINELS = ['zNo_URL', 'zMissing_URL'];

var ACCOUNT_TYPE_ALL_YEARS = 'All';

// A config row is a STATEMENT row (rather than a regular per-year invoice
// row) when its Inv_Account ends with this suffix, e.g. "Credit-Statements".
// The base account ("Credit") is whatever precedes the suffix. Inv_Year on
// a statement row is not a calendar year at all -- it's a literal string
// (e.g. "Acme_1234") that the script expects to find, verbatim, between a
// YYYY-MM-DD date prefix and the file extension. This keeps the script
// itself free of any bank name or assumed suffix format; that detail lives
// entirely in the config table.
var STATEMENT_ACCOUNT_SUFFIX = '-Statements';
var STATEMENT_DATE_PREFIX_PATTERN = /^\d{4}-\d{2}-\d{2}_/;

// Sentinel scope key for a row whose filename looks statement-shaped (uses
// a known bank token) but doesn't match any literal configured for that
// row's own account. This key is never present in folderByScope and is
// treated as always-active by the pre-filter (see runInvoiceRecon), so
// such rows always reach classification and get flagged as
// STATEMENT_ACCOUNT_MISMATCH instead of silently disappearing.
var STATEMENT_MISMATCH_SCOPE_KEY = '__STATEMENT_MISMATCH__';

// Discrepancy type codes
var TYPE = {
  NOT_ENTERED: 'NOT_ENTERED',
  ORPHAN_FILENAME: 'ORPHAN_FILENAME',
  ORPHAN_URL: 'ORPHAN_URL',
  DUPLICATE_FILENAME: 'DUPLICATE_FILENAME',
  DUPLICATE_URL: 'DUPLICATE_URL',
  URL_MISMATCH: 'URL_MISMATCH',
  URL_BROKEN: 'URL_BROKEN',
  MISSING_IN_DRIVE: 'MISSING_IN_DRIVE',
  MISSING_IN_SHEET: 'MISSING_IN_SHEET',
  UNCONFIGURED_FOLDER: 'UNCONFIGURED_FOLDER',
  STATEMENT_ACCOUNT_MISMATCH: 'STATEMENT_ACCOUNT_MISMATCH'
};

// Order in which summary metrics are written
var SUMMARY_METRIC_ORDER = [
  'Last run timestamp',
  'Total ledger rows scanned',
  'Rows skipped (no invoice expected)',
  TYPE.NOT_ENTERED,
  TYPE.ORPHAN_FILENAME,
  TYPE.ORPHAN_URL,
  TYPE.DUPLICATE_FILENAME,
  TYPE.DUPLICATE_URL,
  TYPE.URL_MISMATCH,
  TYPE.URL_BROKEN,
  TYPE.MISSING_IN_DRIVE,
  TYPE.MISSING_IN_SHEET,
  TYPE.UNCONFIGURED_FOLDER,
  TYPE.STATEMENT_ACCOUNT_MISMATCH
];

// ---- Entry point ------------------------------------------------------------

function runInvoiceRecon() {
  var t0 = Date.now();
  function elapsed() { return ((Date.now() - t0) / 1000).toFixed(1) + 's'; }

  var sheetNames = loadSheetNames_();
  var LEDGER_SHEET_NAME = sheetNames.ledger;
  var RECON_SHEET_NAME = sheetNames.recon;

  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ledgerSheet = ss.getSheetByName(LEDGER_SHEET_NAME);
  var reconSheet = ss.getSheetByName(RECON_SHEET_NAME);

  if (!ledgerSheet) throw new Error('Sheet not found: ' + LEDGER_SHEET_NAME);
  if (!reconSheet) throw new Error('Sheet not found: ' + RECON_SHEET_NAME);

  var config = loadFolderConfig_(reconSheet, RECON_SHEET_NAME);
  Logger.log('[%s] config loaded (%s active scopes, %s statement literals)', elapsed(),
    Object.keys(config.folderByScope).length, config.statementLiterals.length);

  if (Object.keys(config.folderByScope).length === 0) {
    Logger.log('No Inv_Active = "Y" rows found on the config table. Nothing to do.');
  }

  var ledgerRows = loadLedgerRows_(ledgerSheet);
  Logger.log('[%s] ledger loaded (%s rows)', elapsed(), ledgerRows.length);

  // Determine each row's scope key up front. A row whose filename matches a
  // known statement literal for its account (see matchStatementLiteral_)
  // routes to that statement config row's scope key (e.g.
  // "Credit-Statements|Acme_1234") regardless of the transaction's own
  // date; everything else routes by Account|Year as before. Statement
  // literals are matched against the FULL config list (active or not) --
  // whether a filename identifies as a given account's statement is a
  // matter of fact, not something Inv_Active should change. Inv_Active
  // only decides whether the resulting scope is actually checked against
  // Drive this run, via isScopeActive_ below.
  //
  // IMPORTANT: a filename that looks statement-shaped (uses a known bank
  // token) but doesn't match any literal configured for THIS row's account
  // must NOT fall back to a normal Account|Year scope key. If it did, the
  // active-scope filter below could silently drop the row on account of
  // that (possibly inactive) year, before classification ever gets a
  // chance to flag it as STATEMENT_ACCOUNT_MISMATCH. Such rows get a
  // dedicated scope key that is always considered active, so they always
  // reach classification.
  ledgerRows.forEach(function (row) {
    var statementMatch = matchStatementLiteral_(config, row.accountType, row.filename);
    if (statementMatch) {
      row.isStatement = true;
      row.scopeKey = statementMatch.scopeKey;
    } else if (looksLikeAnyStatement_(config, row.filename)) {
      row.isStatement = false;
      row.isUnmatchedStatementShape = true;
      row.scopeKey = STATEMENT_MISMATCH_SCOPE_KEY;
    } else {
      row.isStatement = false;
      row.scopeKey = row.accountType + '|' + row.year;
    }
  });

  // Only evaluate ledger rows whose scope is active on the config table.
  // Rows flagged as an unmatched statement shape always pass through here
  // regardless of Inv_Active, since they need to reach classification to be
  // flagged as STATEMENT_ACCOUNT_MISMATCH -- see note above.
  ledgerRows = ledgerRows.filter(function (row) {
    if (row.isUnmatchedStatementShape) return true;
    var separatorIndex = row.scopeKey.indexOf('|');
    var scopeAccount = row.scopeKey.substring(0, separatorIndex);
    var scopeYear = row.scopeKey.substring(separatorIndex + 1);
    return isScopeActive_(config, scopeAccount, scopeYear);
  });
  Logger.log('[%s] filtered to active scopes -> %s rows', elapsed(), ledgerRows.length);

  var discrepancies = [];
  var counts = {};
  SUMMARY_METRIC_ORDER.forEach(function (k) { counts[k] = 0; });
  counts['Total ledger rows scanned'] = ledgerRows.length;

  // ---- Classify each ledger row ----
  var checkableRows = []; // rows with a real filename AND real URL
  ledgerRows.forEach(function (row) {
    var iVal = row.filename;
    var jVal = row.url;
    var iBlank = isBlank_(iVal);
    var jBlank = isBlank_(jVal);
    var iSentinel = isSentinel_(iVal, FILENAME_SENTINELS);
    var jSentinel = isSentinel_(jVal, URL_SENTINELS);
    var iReal = !iBlank && !iSentinel;
    var jReal = !jBlank && !jSentinel;

    if (iBlank && jBlank) {
      addDiscrepancy_(discrepancies, counts, TYPE.NOT_ENTERED, row, '', '',
        'Both Invoice and URL are blank.');
      return;
    }

    if (iSentinel && jSentinel) {
      // No invoice expected for this row -- not a discrepancy.
      counts['Rows skipped (no invoice expected)']++;
      return;
    }

    if (iReal && !jReal) {
      addDiscrepancy_(discrepancies, counts, TYPE.ORPHAN_FILENAME, row, iVal, jVal,
        'Filename present but URL is missing/sentinel.');
      return;
    }

    if (jReal && !iReal) {
      addDiscrepancy_(discrepancies, counts, TYPE.ORPHAN_URL, row, iVal, jVal,
        'URL present but filename is missing/sentinel.');
      return;
    }

    if (iReal && jReal) {
      if (row.isUnmatchedStatementShape) {
        // Matches a known bank token's date-prefixed shape, but not any
        // configured literal for THIS account -- e.g. a Checking row naming
        // a suffix that only exists under Credit-Statements. Flag distinctly
        // rather than letting it fall through as an ordinary invoice check.
        addDiscrepancy_(discrepancies, counts, TYPE.STATEMENT_ACCOUNT_MISMATCH, row,
          row.filename, row.url,
          'Filename matches a known statement bank token but not any configured ' +
          row.accountType + '-Statements literal.');
        return;
      }
      checkableRows.push(row);
      return;
    }

    // Fallback: one blank + one sentinel (mismatched pairing), treat as NOT_ENTERED
    // since neither side has real data.
    addDiscrepancy_(discrepancies, counts, TYPE.NOT_ENTERED, row, iVal, jVal,
      'Invoice/URL pair is incomplete (blank or sentinel mismatch).');
  });
  Logger.log('[%s] classification done (%s checkable rows)', elapsed(), checkableRows.length);

  // ---- Duplicate checks across all checkable rows ----
  var byFilename = {}; // filename -> Set of urls
  var byUrl = {};       // url -> Set of filenames
  checkableRows.forEach(function (row) {
    if (!byFilename[row.filename]) byFilename[row.filename] = {};
    byFilename[row.filename][row.url] = true;

    if (!byUrl[row.url]) byUrl[row.url] = {};
    byUrl[row.url][row.filename] = true;
  });

  var flaggedDupFilenames = {};
  Object.keys(byFilename).forEach(function (fn) {
    var urls = Object.keys(byFilename[fn]);
    if (urls.length > 1) flaggedDupFilenames[fn] = urls;
  });

  var flaggedDupUrls = {};
  Object.keys(byUrl).forEach(function (u) {
    var fns = Object.keys(byUrl[u]);
    if (fns.length > 1) flaggedDupUrls[u] = fns;
  });

  checkableRows.forEach(function (row) {
    if (flaggedDupFilenames[row.filename]) {
      addDiscrepancy_(discrepancies, counts, TYPE.DUPLICATE_FILENAME, row,
        row.filename, row.url,
        'Filename maps to ' + flaggedDupFilenames[row.filename].length + ' distinct URLs.');
    }
    if (flaggedDupUrls[row.url]) {
      addDiscrepancy_(discrepancies, counts, TYPE.DUPLICATE_URL, row,
        row.filename, row.url,
        'URL maps to ' + flaggedDupUrls[row.url].length + ' distinct filenames.');
    }
  });

  Logger.log('[%s] duplicate checks done', elapsed());

  // ---- URL resolution checks (getFileById) ----
  // NOTE: each getFileById() call is a separate Drive API round trip. This
  // loop is the most likely place a large run exceeds the 6-minute Apps
  // Script execution limit -- watch the periodic log lines below to confirm.
  checkableRows.forEach(function (row, i) {
    var fileId = extractDriveFileId_(row.url);
    if (!fileId) {
      addDiscrepancy_(discrepancies, counts, TYPE.URL_BROKEN, row, row.filename, row.url,
        'Could not extract a Drive file ID from the URL.');
      return;
    }
    var file;
    try {
      file = DriveApp.getFileById(fileId);
      // Touch the name to force resolution / confirm access.
      var actualName = file.getName();
      var actualBase = stripExtension_(actualName);
      if (actualBase !== row.filename) {
        addDiscrepancy_(discrepancies, counts, TYPE.URL_MISMATCH, row, row.filename, row.url,
          'URL resolves to "' + actualName + '", expected "' + row.filename + '".');
      }
    } catch (e) {
      addDiscrepancy_(discrepancies, counts, TYPE.URL_BROKEN, row, row.filename, row.url,
        'URL did not resolve to an accessible Drive file (' + e.message + ').');
    }
    if ((i + 1) % 25 === 0) {
      Logger.log('[%s] getFileById progress: %s / %s', elapsed(), i + 1, checkableRows.length);
    }
  });
  Logger.log('[%s] URL resolution checks done', elapsed());

  // ---- Drive-side completeness checks (per scope: Account/Year or Account/Statements-XXXX) ----
  var scopes = {}; // key "Account|Year" or "Account|Statements-XXXX" -> array of rows
  checkableRows.forEach(function (row) {
    if (!scopes[row.scopeKey]) scopes[row.scopeKey] = [];
    scopes[row.scopeKey].push(row);
  });

  Object.keys(scopes).forEach(function (key) {
    var separatorIndex = key.indexOf('|');
    var accountType = key.substring(0, separatorIndex);
    var scopeLabel = key.substring(separatorIndex + 1); // year, "All", or "Statements-XXXX"
    var rowsInScope = scopes[key];

    var folderId = resolveFolderId_(config, accountType, scopeLabel);
    if (!folderId) {
      // Report once per unconfigured scope, not once per row.
      discrepancies.push({
        seq: '', account: accountType, year: scopeLabel, type: TYPE.UNCONFIGURED_FOLDER,
        filename: '', url: '',
        detail: rowsInScope.length + ' ledger row(s) in this scope; no folder configured.'
      });
      counts[TYPE.UNCONFIGURED_FOLDER]++;
      return;
    }

    var folder;
    try {
      folder = DriveApp.getFolderById(folderId);
    } catch (e) {
      discrepancies.push({
        seq: '', account: accountType, year: scopeLabel, type: TYPE.UNCONFIGURED_FOLDER,
        filename: '', url: '',
        detail: 'Configured Folder ID does not resolve (' + e.message + ').'
      });
      counts[TYPE.UNCONFIGURED_FOLDER]++;
      return;
    }

    // Build the set of actual files in the Drive folder, keyed by base name.
    // NOTE: getFiles() pages through the ENTIRE folder every time this scope
    // runs. If a folder holds many years' worth of files (e.g. Checking's
    // single "All years" invoice folder, or either statements folder), this
    // can be slow even for a narrowly-scoped run.
    var driveFilesByBase = {}; // baseName -> array of actual file names (handles case dupes)
    var files = folder.getFiles();
    var fileCount = 0;
    while (files.hasNext()) {
      var f = files.next();
      var base = stripExtension_(f.getName());
      if (!driveFilesByBase[base]) driveFilesByBase[base] = [];
      driveFilesByBase[base].push(f.getName());
      fileCount++;
      if (fileCount % 100 === 0) {
        Logger.log('[%s] scanning folder %s|%s: %s files so far', elapsed(), accountType, scopeLabel, fileCount);
      }
    }
    Logger.log('[%s] folder %s|%s scan complete: %s files total', elapsed(), accountType, scopeLabel, fileCount);

    // Ledger filenames expected in this scope.
    var ledgerFilenamesInScope = {};
    rowsInScope.forEach(function (row) {
      ledgerFilenamesInScope[row.filename] = true;
    });

    // MISSING_IN_DRIVE: ledger says it exists, Drive folder has nothing matching.
    rowsInScope.forEach(function (row) {
      if (!driveFilesByBase[row.filename]) {
        addDiscrepancy_(discrepancies, counts, TYPE.MISSING_IN_DRIVE, row,
          row.filename, row.url,
          'No file named "' + row.filename + '" (any extension) found in the ' +
          accountType + ' / ' + scopeLabel + ' folder.');
      }
    });

    // MISSING_IN_SHEET: Drive has a file, no ledger row in scope references it.
    Object.keys(driveFilesByBase).forEach(function (base) {
      if (!ledgerFilenamesInScope[base]) {
        driveFilesByBase[base].forEach(function (actualName) {
          discrepancies.push({
            seq: '', account: accountType, year: scopeLabel, type: TYPE.MISSING_IN_SHEET,
            filename: base, url: '',
            detail: 'Drive file "' + actualName + '" is not referenced by any ledger row in this scope.'
          });
          counts[TYPE.MISSING_IN_SHEET]++;
        });
      }
    });
  });

  Logger.log('[%s] Drive-side completeness checks done', elapsed());

  // ---- Write output ----
  writeSummary_(reconSheet, counts);
  writeDetail_(reconSheet, discrepancies);
  Logger.log('[%s] output written, %s discrepancies', elapsed(), discrepancies.length);

  return {
    counts: counts,
    discrepancyCount: discrepancies.length
  };
}

// ---- Config loading ---------------------------------------------------------

/**
 * Reads the ledger and recon tab names from Script Properties, so they
 * never need to be hardcoded in source. Set these once per spreadsheet
 * under the Apps Script editor's Project Settings > Script Properties:
 *
 *   LEDGER_SHEET_NAME   e.g. "Household Ledger"
 *   RECON_SHEET_NAME    e.g. "Reconciliation"
 *
 * Throws a clear error (rather than a confusing "Sheet not found" later)
 * if either property hasn't been set yet.
 */
function loadSheetNames_() {
  var props = PropertiesService.getScriptProperties();
  var ledger = props.getProperty('LEDGER_SHEET_NAME');
  var recon = props.getProperty('RECON_SHEET_NAME');

  var missing = [];
  if (!ledger) missing.push('LEDGER_SHEET_NAME');
  if (!recon) missing.push('RECON_SHEET_NAME');

  if (missing.length > 0) {
    throw new Error('Missing Script Propert' + (missing.length > 1 ? 'ies' : 'y') + ': ' +
      missing.join(', ') + '. Set ' + (missing.length > 1 ? 'these' : 'this') +
      ' under Project Settings > Script Properties in the Apps Script editor.');
  }

  return { ledger: ledger, recon: recon };
}

/**
 * Reads the folder config table.
 *
 * Returns:
 *   {
 *     folderByScope: { "Account|Year": folderId, ... }        // ACTIVE rows only
 *     statementLiterals: [ { account: "Credit", literal: "Acme_1234",
 *                            scopeKey: "Credit-Statements|Acme_1234" }, ... ]
 *     statementBankTokens: { "Acme": true, ... }
 *   }
 *
 * folderByScope drives Drive I/O and only ever contains rows where
 * Inv_Active = "Y" -- this is what keeps a run time-boxed to whatever
 * scopes you've turned on.
 *
 * statementLiterals and statementBankTokens drive CLASSIFICATION (deciding
 * whether a ledger row's filename is a statement, and which account it
 * belongs to) and are built from every Credit-Statements /
 * Checking-Statements / etc. config row regardless of Inv_Active, since
 * whether a filename identifies as a given account's statement isn't
 * something that should change based on whether this run happens to be
 * checking that scope against Drive.
 *
 * statementBankTokens is the set of bank names actually in use (the part of
 * each literal before its last underscore, e.g. "Acme" from "Acme_1234").
 * This is what lets the script recognize "this filename is statement-shaped"
 * without hardcoding any bank name: only filenames using a bank token that
 * appears somewhere in your own config are ever treated as statements. An
 * ordinary invoice like "2021-02-08_SomeVendor" is left alone because
 * "SomeVendor" was never configured as a statement bank token.
 */
function loadFolderConfig_(reconSheet, reconSheetName) {
  var headerRange = reconSheet.getRange(CONFIG_HEADER_ROW, CONFIG_START_COL, 1, 5);
  var headers = headerRange.getValues()[0];
  var colIndex = mapHeaders_(headers, ['Inv_Active', 'Inv_Account', 'Inv_Year', 'Inv_Folder', 'Inv_FolderURL'], reconSheetName);

  var lastRow = reconSheet.getLastRow();
  var numDataRows = Math.max(0, lastRow - CONFIG_DATA_START_ROW + 1);
  var folderByScope = {};
  var statementLiterals = [];
  var statementBankTokens = {};

  if (numDataRows === 0) {
    return { folderByScope: folderByScope, statementLiterals: statementLiterals, statementBankTokens: statementBankTokens };
  }

  var data = reconSheet.getRange(CONFIG_DATA_START_ROW, CONFIG_START_COL, numDataRows, 5).getValues();
  data.forEach(function (row) {
    var active = String(row[colIndex.Inv_Active] || '').trim().toUpperCase();
    var configAccount = String(row[colIndex.Inv_Account] || '').trim();
    var configYear = String(row[colIndex.Inv_Year] || '').trim();
    var folderIdRaw = String(row[colIndex.Inv_Folder] || '').trim();

    if (!configAccount || !configYear) return; // skip blank config rows

    if (endsWith_(configAccount, STATEMENT_ACCOUNT_SUFFIX)) {
      var baseAccount = configAccount.substring(0, configAccount.length - STATEMENT_ACCOUNT_SUFFIX.length);
      statementLiterals.push({
        account: baseAccount,
        literal: configYear,
        scopeKey: configAccount + '|' + configYear
      });

      var lastUnderscore = configYear.lastIndexOf('_');
      if (lastUnderscore > 0) {
        var bankToken = configYear.substring(0, lastUnderscore);
        statementBankTokens[bankToken] = true;
      }
    }

    if (active !== 'Y') return; // inactive scope, skip for Drive I/O purposes
    if (!folderIdRaw) return; // active but not yet configured; treat as unconfigured

    var folderId = extractDriveFolderId_(folderIdRaw);
    folderByScope[configAccount + '|' + configYear] = folderId;
  });

  return { folderByScope: folderByScope, statementLiterals: statementLiterals, statementBankTokens: statementBankTokens };
}

/**
 * True if filename (already extension-stripped) has the shape
 * YYYY-MM-DD_<known bank token>_<anything>, using only bank tokens that
 * actually appear in the config table. This is the signal used to decide
 * whether an unmatched filename deserves a STATEMENT_ACCOUNT_MISMATCH flag
 * rather than being treated as an ordinary invoice -- an invoice that
 * merely starts with a date (e.g. "2021-02-08_SomeVendor") does not use
 * any configured bank token and so is left alone.
 */
function looksLikeAnyStatement_(config, filename) {
  var prefixMatch = filename.match(STATEMENT_DATE_PREFIX_PATTERN);
  if (!prefixMatch) return false;
  var remainder = filename.substring(prefixMatch[0].length);
  var tokens = Object.keys(config.statementBankTokens);
  return tokens.some(function (token) {
    return remainder === token || remainder.indexOf(token + '_') === 0;
  });
}

/**
 * If filename (already extension-stripped) matches YYYY-MM-DD_<literal> for
 * some known statement literal belonging to accountType, returns
 * { scopeKey, literal }. Otherwise returns null.
 *
 * Only literals registered for this exact accountType are considered, so a
 * Checking row can never match a Credit statement literal or vice versa.
 */
function matchStatementLiteral_(config, accountType, filename) {
  var found = null;
  config.statementLiterals.some(function (candidate) {
    if (candidate.account !== accountType) return false;
    var expected = candidate.literal;
    var prefixMatch = filename.match(STATEMENT_DATE_PREFIX_PATTERN);
    if (!prefixMatch) return false;
    var remainder = filename.substring(prefixMatch[0].length);
    if (remainder === expected) {
      found = candidate;
      return true; // stop iterating, found a match
    }
    return false;
  });
  return found;
}

/**
 * Looks up a folder ID for a scope key ("Account|Year", "Account|All", or
 * "Account-Statements|<literal>"), falling back to that account's "All
 * years" row if an exact year match isn't found. The fallback only ever
 * applies to genuine calendar-year lookups: a statement scope key's account
 * portion (e.g. "Credit-Statements") is a different string from the plain
 * account ("Credit"), so it can never collide with that account's regular
 * "All years" invoice folder.
 */
function resolveFolderId_(config, accountKey, year) {
  var exact = config.folderByScope[accountKey + '|' + year];
  if (exact) return exact;
  var allYears = config.folderByScope[accountKey + '|' + ACCOUNT_TYPE_ALL_YEARS];
  if (allYears) return allYears;
  return null;
}

/**
 * True if the given scope key is turned on via Inv_Active = "Y", either as
 * an exact match or via that account's "All years" row.
 */
function isScopeActive_(config, accountKey, year) {
  if (config.folderByScope.hasOwnProperty(accountKey + '|' + year)) return true;
  if (config.folderByScope.hasOwnProperty(accountKey + '|' + ACCOUNT_TYPE_ALL_YEARS)) return true;
  return false;
}

// ---- Ledger loading ---------------------------------------------------------

function loadLedgerRows_(ledgerSheet) {
  var lastRow = ledgerSheet.getLastRow();
  var numDataRows = Math.max(0, lastRow - LEDGER_DATA_START_ROW + 1);
  if (numDataRows === 0) return [];

  var firstCol = Math.min(LEDGER_COL.SEQ, LEDGER_COL.DATE, LEDGER_COL.ACCOUNT, LEDGER_COL.FILENAME, LEDGER_COL.URL);
  var lastCol = Math.max(LEDGER_COL.SEQ, LEDGER_COL.DATE, LEDGER_COL.ACCOUNT, LEDGER_COL.FILENAME, LEDGER_COL.URL);
  var numCols = lastCol - firstCol + 1;

  var data = ledgerSheet.getRange(LEDGER_DATA_START_ROW, firstCol, numDataRows, numCols).getValues();

  var rows = [];
  data.forEach(function (raw, i) {
    var seq = raw[LEDGER_COL.SEQ - firstCol];
    var dateVal = raw[LEDGER_COL.DATE - firstCol];
    var accountRaw = String(raw[LEDGER_COL.ACCOUNT - firstCol] || '').trim();
    var filenameRaw = raw[LEDGER_COL.FILENAME - firstCol];
    var urlRaw = raw[LEDGER_COL.URL - firstCol];

    // Skip fully blank rows (e.g. trailing empty rows in the range).
    if (!seq && !dateVal && !accountRaw && !filenameRaw && !urlRaw) return;

    var year = extractYear_(dateVal);
    var accountType = classifyAccountType_(accountRaw);

    rows.push({
      sheetRow: LEDGER_DATA_START_ROW + i,
      seq: seq,
      accountRaw: accountRaw,
      accountType: accountType,
      year: year,
      filename: filenameRaw === null || filenameRaw === undefined ? '' : String(filenameRaw).trim(),
      url: urlRaw === null || urlRaw === undefined ? '' : String(urlRaw).trim()
    });
  });

  return rows;
}

function classifyAccountType_(accountRaw) {
  if (accountRaw === 'Credit') return 'Credit';
  if (accountRaw === 'Checking') return 'Checking';
  return 'Other';
}

function extractYear_(dateVal) {
  if (dateVal instanceof Date && !isNaN(dateVal.getTime())) {
    return String(dateVal.getFullYear());
  }
  // Fallback: try to parse a year out of a text date.
  var s = String(dateVal || '');
  var match = s.match(/(19|20)\d{2}/);
  return match ? match[0] : '';
}

// ---- Classification helpers -------------------------------------------------

function isBlank_(val) {
  return val === null || val === undefined || String(val).trim() === '';
}

function isSentinel_(val, sentinelList) {
  if (isBlank_(val)) return false;
  var v = String(val).trim();
  return sentinelList.indexOf(v) !== -1;
}

function endsWith_(str, suffix) {
  if (str.length < suffix.length) return false;
  return str.substring(str.length - suffix.length) === suffix;
}

function addDiscrepancy_(discrepancies, counts, type, row, filename, url, detail) {
  discrepancies.push({
    seq: row.seq,
    account: row.accountType,
    year: row.year,
    type: type,
    filename: filename,
    url: url,
    detail: detail
  });
  counts[type]++;
}

// ---- Drive helpers -----------------------------------------------------------

/**
 * Extracts a Drive file ID from common share-link formats:
 *   https://drive.google.com/file/d/FILE_ID/view?usp=sharing
 *   https://drive.google.com/open?id=FILE_ID
 *   https://docs.google.com/document/d/FILE_ID/edit
 * Falls back to treating the whole string as an ID if nothing else matches.
 */
function extractDriveFileId_(url) {
  if (!url) return null;
  var s = String(url).trim();

  var m = s.match(/\/d\/([a-zA-Z0-9_-]{10,})/);
  if (m) return m[1];

  m = s.match(/[?&]id=([a-zA-Z0-9_-]{10,})/);
  if (m) return m[1];

  // If it looks like a bare ID already (no slashes/protocol), use it directly.
  if (/^[a-zA-Z0-9_-]{10,}$/.test(s)) return s;

  return null;
}

/**
 * Same idea as extractDriveFileId_ but for folder links, and also accepts
 * a bare folder ID typed directly into the config sheet.
 */
function extractDriveFolderId_(urlOrId) {
  var s = String(urlOrId).trim();
  var m = s.match(/\/folders\/([a-zA-Z0-9_-]{10,})/);
  if (m) return m[1];
  if (/^[a-zA-Z0-9_-]{10,}$/.test(s)) return s;
  return s; // last resort, let getFolderById surface the error
}

function stripExtension_(name) {
  var idx = name.lastIndexOf('.');
  if (idx <= 0) return name; // no extension, or dotfile with nothing before it
  return name.substring(0, idx);
}

// ---- Header mapping helper ---------------------------------------------------

function mapHeaders_(headerRow, expectedHeaders, reconSheetName) {
  var idx = {};
  expectedHeaders.forEach(function (h) {
    var pos = headerRow.indexOf(h);
    if (pos === -1) {
      throw new Error('Expected header "' + h + '" not found in row ' + CONFIG_HEADER_ROW +
        ' of ' + reconSheetName + '. Found: ' + headerRow.join(', '));
    }
    idx[h] = pos;
  });
  return idx;
}

// ---- Output writers ------------------------------------------------------------

var SUMMARY_HEADERS = ['Metric', 'Count'];
var DETAIL_HEADERS = ['Seq', 'Account', 'Year', 'Type', 'Filename', 'URL', 'Detail'];

function writeSummary_(reconSheet, counts) {
  // Headers (row 5) -- rewritten every run so the sheet self-heals if ever cleared.
  reconSheet.getRange(SUMMARY_HEADER_ROW, SUMMARY_START_COL, 1, SUMMARY_HEADERS.length)
    .setValues([SUMMARY_HEADERS])
    .setFontWeight('bold');

  // Clear old summary output first.
  reconSheet.getRange(SUMMARY_DATA_START_ROW, SUMMARY_START_COL, SUMMARY_ROWS_TO_CLEAR, 2).clearContent();

  var rows = SUMMARY_METRIC_ORDER.map(function (metric) {
    if (metric === 'Last run timestamp') {
      return [metric, Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm:ss')];
    }
    return [metric, counts[metric] || 0];
  });

  reconSheet.getRange(SUMMARY_DATA_START_ROW, SUMMARY_START_COL, rows.length, 2).setValues(rows);
}

function writeDetail_(reconSheet, discrepancies) {
  // Headers (row 5) -- rewritten every run so the sheet self-heals if ever cleared.
  reconSheet.getRange(DETAIL_HEADER_ROW, DETAIL_START_COL, 1, DETAIL_HEADERS.length)
    .setValues([DETAIL_HEADERS])
    .setFontWeight('bold');

  // Clear old detail output first (generous fixed range).
  reconSheet.getRange(DETAIL_DATA_START_ROW, DETAIL_START_COL, DETAIL_ROWS_TO_CLEAR, DETAIL_COLS).clearContent();

  if (discrepancies.length === 0) return;

  var rows = discrepancies.map(function (d) {
    return [d.seq, d.account, d.year, d.type, d.filename, d.url, d.detail];
  });

  reconSheet.getRange(DETAIL_DATA_START_ROW, DETAIL_START_COL, rows.length, DETAIL_COLS).setValues(rows);
}
