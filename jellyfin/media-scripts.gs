// ============================================================
// Jellyfin Segment Sync — Google Apps Script
// ============================================================
// CONFIGURATION — update these two values before running
const props = PropertiesService.getScriptProperties();
const JELLYFIN_URL     = props.getProperty('JELLYFIN_URL');
const JELLYFIN_API_KEY = props.getProperty('JELLYFIN_API_KEY');
const SHEET_NAME_TV    = props.getProperty('SHEET_NAME_TV');
const TMDB_API_KEY     = props.getProperty('TMDB_API_KEY');
const SHEET_NAME_MOVIE = 'Movie';

// Sheet config
const DATA_START_ROW = 2;

// Column indices (1-based)
const COL_SHOW          =  1; // A
const COL_TVDB          =  2; // B
const COL_SEASON        =  3; // C
const COL_EPISODE       =  4; // D
const COL_TITLE         =  5; // E
const COL_ITEMID        =  6; // F
const COL_INTRO_START   = 15; // O
const COL_INTRO_END     = 16; // P
const COL_RECAP_START   = 17; // Q
const COL_RECAP_END     = 18; // R
const COL_CREDITS_START = 19; // S
const COL_CREDITS_END   = 20; // T

// Movie sheet column indices (1-based)
const COL_MOVIE_TITLE   =  1; // A
const COL_MOVIE_YEAR    =  2; // B
const COL_MOVIE_TMDB    =  3; // C
const COL_MOVIE_COLLECTION = 8; // H

// TMDb validation config
const TMDB_BATCH_SIZE   = 20;        // parallel requests per UrlFetchApp.fetchAll
const TMDB_INVALID_BG   = "#f4cccc"; // light red highlight for failed validation

// ============================================================
// MENU
// ============================================================
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu("Jellyfin")
    .addItem("Sync All Rows", "syncAll")
    .addItem("Lookup Missing ItemIDs Only", "lookupItemIds")
    .addItem("Push Segments Only", "pushSegments")
    .addSeparator()
    .addItem("TMDb Validate", "tmdbValidate")
    .addToUi();
}

// ============================================================
// MAIN ENTRY POINTS
// ============================================================

// Run both passes on all rows
function syncAll() {
  lookupItemIds();
  pushSegments();
}

// Pass 1 — fill in any blank ItemIds by searching Jellyfin
function lookupItemIds() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME_TV);
  const lastRow = sheet.getLastRow();
  if (lastRow < DATA_START_ROW) return;

  const allSeries = fetchAllJellyfinSeries();
  if (!allSeries) {
    Logger.log("ItemId Lookup aborted — could not fetch series list from Jellyfin");
    return;
  }

  const lastCol = sheet.getLastColumn();
  const rows = sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).getValues();
  let lookupCount = 0;
  let failCount = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const rowNum = DATA_START_ROW + i;
    const itemId = String(row[COL_ITEMID - 1]).trim();

    // Skip if ItemId already populated
    if (itemId !== "") continue;

    // All columns except ItemID must be populated
    if (!row.every((cell, idx) => idx === COL_ITEMID - 1 || String(cell).trim() !== "")) continue;

    const show    = String(row[COL_SHOW - 1]).trim();
    const season  = row[COL_SEASON - 1];
    const episode = row[COL_EPISODE - 1];

    const { id: foundId, reason } = searchJellyfinItem(allSeries, show, season, episode);

    if (foundId) {
      sheet.getRange(rowNum, COL_ITEMID).setValue(foundId);
      lookupCount++;
    } else {
      Logger.log(`Row ${rowNum}: itemId not found — show="${show}" S${season}E${episode} — ${reason}`);
      failCount++;
    }

    Utilities.sleep(500); // be gentle on the API
  }

  Logger.log(`ItemId Lookup Complete — Found: ${lookupCount} | Not found: ${failCount}`);
  SpreadsheetApp.getActiveSpreadsheet().toast(
    `Found: ${lookupCount} | Not found: ${failCount}`,
    "ItemId Lookup Complete",
    10
  );
}

// Pass 2 — push segment times to Jellyfin for rows with an ItemId and at least one segment
function pushSegments() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME_TV);
  const lastRow = sheet.getLastRow();
  if (lastRow < DATA_START_ROW) return;

  const lastCol = sheet.getLastColumn();
  const rows = sheet.getRange(DATA_START_ROW, 1, lastRow - DATA_START_ROW + 1, lastCol).getValues();
  let syncCount = 0;
  let skipCount = 0;
  let failCount = 0;

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const rowNum = DATA_START_ROW + i;
    const itemId  = String(row[COL_ITEMID - 1]).trim();

    const introStart   = row[COL_INTRO_START - 1];
    const introEnd     = row[COL_INTRO_END - 1];
    const recapStart   = row[COL_RECAP_START - 1];
    const recapEnd     = row[COL_RECAP_END - 1];
    const creditsStart = row[COL_CREDITS_START - 1];
    const creditsEnd   = row[COL_CREDITS_END - 1];

    // All columns must be populated
    if (!row.every(cell => String(cell).trim() !== "")) {
      skipCount++;
      continue;
    }

    // Skip if every segment column is blank or n/a
    if (![introStart, introEnd, recapStart, recapEnd, creditsStart, creditsEnd].some(v => isPopulated(v))) {
      skipCount++;
      continue;
    }

    // For "end" credits, look up the episode runtime before building the body
    let runtimeSeconds;
    if (String(creditsEnd).trim().toLowerCase() === "end") {
      const ticks = getItemRuntimeTicks(itemId);
      if (ticks === null) {
        Logger.log(`Row ${rowNum}: could not fetch runtime for ${itemId}`);
        failCount++;
        continue;
      }
      runtimeSeconds = ticks / 10000000;
      Utilities.sleep(500);
    }

    const body = buildSegmentBody(itemId, introStart, introEnd, recapStart, recapEnd, creditsStart, creditsEnd, runtimeSeconds);
    if (Object.keys(body).length === 0) { skipCount++; continue; }
    const result = postTimestamps(itemId, body);

    if (result.success) {
      syncCount++;
    } else {
      failCount++;
    }

    Utilities.sleep(500);
  }

  Logger.log(`Segment Sync Complete — Synced: ${syncCount} | Skipped: ${skipCount} | Failed: ${failCount}`);
  SpreadsheetApp.getActiveSpreadsheet().toast(
    `Synced: ${syncCount} | Skipped: ${skipCount} | Failed: ${failCount}`,
    "Segment Sync Complete",
    10
  );
}

// ============================================================
// TMDB VALIDATION
// ============================================================

// Run all TMDb validations against the Movie sheet. Each unique TMDb ID is fetched once
// and the result is shared by every check. Failed cells are highlighted red; a cell we
// previously flagged that now passes is cleared.
function tmdbValidate() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME_MOVIE);
  const lastRow = sheet.getLastRow();
  if (lastRow < DATA_START_ROW) return;

  const numRows = lastRow - DATA_START_ROW + 1;
  const rows = sheet.getRange(DATA_START_ROW, 1, numRows, COL_MOVIE_COLLECTION).getValues();

  // Each check reads its column from the row, compares against the TMDb movie,
  // and returns { ok, detail } — or null to skip the row.
  const checks = [
    { name: "Year",       col: COL_MOVIE_YEAR,       fn: checkYear },
    { name: "Collection", col: COL_MOVIE_COLLECTION, fn: checkCollection },
  ];
  checks.forEach(check => {
    check.range = sheet.getRange(DATA_START_ROW, check.col, numRows, 1);
    check.backgrounds = check.range.getBackgrounds();
    check.okCount = 0;
    check.mismatchCount = 0;
  });

  const uniqueIds = [...new Set(rows.map(row => parseTmdbId(row[COL_MOVIE_TMDB - 1])).filter(id => id))];
  const movies = fetchTmdbMovies(uniqueIds);
  let errorCount = 0;

  for (let i = 0; i < rows.length; i++) {
    const row    = rows[i];
    const rowNum = DATA_START_ROW + i;
    const tmdbId = parseTmdbId(row[COL_MOVIE_TMDB - 1]);
    if (!tmdbId) continue;

    const title = row[COL_MOVIE_TITLE - 1];
    const { movie, error } = movies[tmdbId];
    if (error) {
      Logger.log(`Row ${rowNum}: "${title}" TMDb ${tmdbId} — ${error}`);
      errorCount++;
      continue;
    }

    checks.forEach(check => {
      const result = check.fn(row[check.col - 1], movie);
      if (!result) return;

      if (result.ok) {
        // Clear our own highlight if the value has since been fixed; leave other formatting alone
        if (check.backgrounds[i][0].toLowerCase() === TMDB_INVALID_BG) {
          check.backgrounds[i][0] = null;
        }
        check.okCount++;
      } else {
        Logger.log(`Row ${rowNum}: "${title}" ${check.name} — ${result.detail}`);
        check.backgrounds[i][0] = TMDB_INVALID_BG;
        check.mismatchCount++;
      }
    });
  }

  checks.forEach(check => check.range.setBackgrounds(check.backgrounds));

  const summary = checks.map(c => `${c.name} ✓${c.okCount} ✗${c.mismatchCount}`).join(" | ")
    + ` | Errors: ${errorCount}`;
  Logger.log(`TMDb Validation Complete — ${summary}`);
  SpreadsheetApp.getActiveSpreadsheet().toast(summary, "TMDb Validation Complete", 10);
}

// Year must match the TMDb release year. Blank years are skipped.
function checkYear(value, movie) {
  const year = String(value).trim();
  if (year === "") return null;
  const tmdbYear = (movie.release_date || "").substring(0, 4);
  return {
    ok: year === tmdbYear,
    detail: `sheet "${year}" ≠ TMDb "${tmdbYear || "no release date"}" (${movie.title})`
  };
}

// Collection must match the TMDb collection name exactly, or "None" if the movie isn't in one
function checkCollection(value, movie) {
  const collection = String(value).trim();
  const tmdbCollection = movie.belongs_to_collection ? movie.belongs_to_collection.name : "None";
  return {
    ok: collection === tmdbCollection,
    detail: `sheet "${collection}" ≠ TMDb "${tmdbCollection}"`
  };
}

function parseTmdbId(v) {
  return String(v).replace(/\D/g, "");
}

// ============================================================
// TMDB API HELPERS
// ============================================================

// Fetch movies in parallel batches. Returns { [tmdbId]: { movie, error } }.
function fetchTmdbMovies(tmdbIds) {
  const results = {};

  for (let b = 0; b < tmdbIds.length; b += TMDB_BATCH_SIZE) {
    const batch = tmdbIds.slice(b, b + TMDB_BATCH_SIZE);
    let responses;
    try {
      responses = UrlFetchApp.fetchAll(batch.map(id => tmdbRequest(`/movie/${id}`)));
    } catch (e) {
      batch.forEach(id => results[id] = { movie: null, error: `exception: ${e.message}` });
      continue;
    }

    responses.forEach((resp, j) => {
      const code = resp.getResponseCode();
      if (code !== 200) {
        results[batch[j]] = { movie: null, error: `HTTP ${code}` };
        return;
      }
      try {
        results[batch[j]] = { movie: JSON.parse(resp.getContentText()), error: null };
      } catch (e) {
        results[batch[j]] = { movie: null, error: `bad JSON: ${e.message}` };
      }
    });

    Utilities.sleep(250); // stay well under TMDb rate limits
  }

  return results;
}

// Supports either a v3 API key or a v4 read access token (JWT, starts with "eyJ")
function tmdbRequest(path) {
  const base = `https://api.themoviedb.org/3${path}?language=en-US`;
  if (TMDB_API_KEY.startsWith("eyJ")) {
    return {
      url: base,
      headers: { "Authorization": `Bearer ${TMDB_API_KEY}` },
      muteHttpExceptions: true
    };
  }
  return { url: `${base}&api_key=${TMDB_API_KEY}`, muteHttpExceptions: true };
}

// ============================================================
// JELLYFIN API HELPERS
// ============================================================

function fetchAllJellyfinSeries() {
  try {
    const url = `${JELLYFIN_URL}/Items?IncludeItemTypes=Series&Recursive=true&Limit=500`;
    const resp = UrlFetchApp.fetch(url, {
      headers: { "Authorization": `MediaBrowser Token="${JELLYFIN_API_KEY}"` },
      muteHttpExceptions: true
    });
    if (resp.getResponseCode() !== 200) {
      Logger.log(`fetchAllJellyfinSeries HTTP ${resp.getResponseCode()}`);
      return null;
    }
    const data = JSON.parse(resp.getContentText());
    return data.Items || [];
  } catch (e) {
    Logger.log(`fetchAllJellyfinSeries exception: ${e.message}`);
    return null;
  }
}

function searchJellyfinItem(allSeries, show, season, episode) {
  try {
    const normalize = s => s.toLowerCase().replace(/[^\w\s]/g, "").replace(/\s+/g, " ").trim();
    const normalizedShow = normalize(show);
    const series = allSeries.find(item =>
      item.Name && normalize(item.Name).includes(normalizedShow)
    );
    if (!series) return { id: null, reason: `no series found matching "${show}" among ${allSeries.length} series in library` };
    const seriesId = series.Id;

    // Fetch episodes from the target season
    const episodesUrl = `${JELLYFIN_URL}/Shows/${seriesId}/Episodes?SeasonNumber=${parseInt(season)}`;
    const episodesResp = UrlFetchApp.fetch(episodesUrl, {
      headers: { "Authorization": `MediaBrowser Token="${JELLYFIN_API_KEY}"` },
      muteHttpExceptions: true
    });
    if (episodesResp.getResponseCode() !== 200) return { id: null, reason: `episodes fetch HTTP ${episodesResp.getResponseCode()} for seriesId=${seriesId}` };

    const episodesData = JSON.parse(episodesResp.getContentText());
    if (!episodesData.Items || episodesData.Items.length === 0) return { id: null, reason: `no episodes returned for S${season} of seriesId=${seriesId}` };

    const s = parseInt(season);
    const e = parseInt(episode);
    const match = episodesData.Items.find(item =>
      item.ParentIndexNumber === s && item.IndexNumber === e
    );
    if (!match) return { id: null, reason: `S${s}E${e} not found among ${episodesData.Items.length} episode(s) returned for seriesId=${seriesId}` };
    return { id: match.Id, reason: null };

  } catch (e) {
    return { id: null, reason: `exception: ${e.message}` };
  }
}

function buildSegmentBody(itemId, introStart, introEnd, recapStart, recapEnd, creditsStart, creditsEnd, runtimeSeconds) {
  const body = {};

  if (isPopulated(introStart) && isPopulated(introEnd)) {
    body.Introduction = { EpisodeId: itemId, Start: toSeconds(introStart), End: toSeconds(introEnd), Valid: true };
  }
  if (isPopulated(recapStart) && isPopulated(recapEnd)) {
    body.Recap = { EpisodeId: itemId, Start: toSeconds(recapStart), End: toSeconds(recapEnd), Valid: true };
  }

  if (isPopulated(creditsStart)) {
    const creditsEndIsEof = String(creditsEnd).trim().toLowerCase() === "end";
    const endSeconds = creditsEndIsEof ? runtimeSeconds : toSeconds(creditsEnd);
    if (endSeconds !== undefined && endSeconds !== null) {
      body.Credits = { EpisodeId: itemId, Start: toSeconds(creditsStart), End: endSeconds, Valid: true };
    }
  }

  return body;
}

function getItemRuntimeTicks(itemId) {
  try {
    const url = `${JELLYFIN_URL}/Items?Ids=${itemId}`;
    const response = UrlFetchApp.fetch(url, {
      headers: { "Authorization": `MediaBrowser Token="${JELLYFIN_API_KEY}"` },
      muteHttpExceptions: true
    });
    if (response.getResponseCode() !== 200) return null;
    const data = JSON.parse(response.getContentText());
    if (!data.Items || data.Items.length === 0) return null;
    return data.Items[0].RunTimeTicks ?? null;
  } catch (e) {
    Logger.log(`Runtime fetch error: ${e.message}`);
    return null;
  }
}

function postTimestamps(itemId, body) {
  try {
    const url = `${JELLYFIN_URL}/Episode/${itemId}/Timestamps`;
    const response = UrlFetchApp.fetch(url, {
      method: "post",
      headers: {
        "Authorization": `MediaBrowser Token="${JELLYFIN_API_KEY}"`,
        "Content-Type": "application/json"
      },
      payload: JSON.stringify(body),
      muteHttpExceptions: true
    });

    const code = response.getResponseCode();
    if (code === 200 || code === 204) {
      return { success: true };
    } else {
      return { success: false, error: `HTTP ${code}: ${response.getContentText().substring(0, 100)}` };
    }
  } catch (e) {
    return { success: false, error: e.message };
  }
}

// ============================================================
// UTILITIES
// ============================================================

function hasValue(v) {
  return v !== "" && v !== null && v !== undefined && !isNaN(Number(v));
}

// Accepts plain seconds (e.g. 90), MM:SS (e.g. 1:30), or HH:MM:SS (e.g. 1:30:00) with optional decimals
function toSeconds(v) {
  const s = String(v).trim();
  if (s === "" || s.toLowerCase() === "n/a" || s.toLowerCase() === "end") return null;

  // If it contains a colon, parse as time
  if (s.indexOf(":") !== -1) {
    const parts = s.split(":").map(Number);
    if (parts.some(isNaN)) return null;
    if (parts.length === 2) {
      // MM:SS
      return parts[0] * 60 + parts[1];
    } else if (parts.length === 3) {
      // HH:MM:SS
      return parts[0] * 3600 + parts[1] * 60 + parts[2];
    }
    return null;
  }

  // Plain number
  const n = Number(s);
  return isNaN(n) ? null : n;
}

function isPopulated(v) {
  const s = String(v).trim().toLowerCase();
  if (s === "" || s === "n/a") return false;
  if (s === "end") return true; // handled separately for creditsEnd
  return toSeconds(v) !== null;
}
