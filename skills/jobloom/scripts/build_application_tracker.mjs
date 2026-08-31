#!/usr/bin/env node
/** Build the human-readable Jobloom application tracker from deterministic JSON state. */

import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";


const runtimeRequire = createRequire(path.join(process.cwd(), "package.json"));
const artifactTool = await import(pathToFileURL(runtimeRequire.resolve("@oai/artifact-tool")).href);
const { SpreadsheetFile, Workbook } = artifactTool;


function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("usage: build_application_tracker.mjs --input tracker.json --output applications.xlsx --preview preview.png");
    }
    values[key.slice(2)] = value;
  }
  if (!values.input || !values.output) {
    throw new Error("--input and --output are required");
  }
  return values;
}


const args = parseArgs(process.argv.slice(2));
const source = JSON.parse(await fs.readFile(args.input, "utf8"));
if (!Array.isArray(source.applications) || source.row_count !== source.applications.length) {
  throw new Error("tracker source row_count does not match applications");
}
const savedSource = source.saved_jobs ?? [];
if (!Array.isArray(savedSource)
    || (source.saved_row_count ?? savedSource.length) !== savedSource.length) {
  throw new Error("tracker source saved_row_count does not match saved_jobs");
}

// "Application URL" was always the posting's own address, which is the link you want when
// you go back to read the description — so it is named for what it is.
const headers = [
  "Submission Time", "Employer", "Role", "Location", "Work Arrangement", "Source", "ATS",
  "Job URL", "Posted", "Days Open", "Resume Version", "Cover Letter Version", "Category",
  "Current Status", "Confirmation ID", "Follow-up Date", "Model Usage", "Archive ID",
  "Archive Path",
];
const rows = source.applications.map((item) => [
  item.submission_time ? new Date(item.submission_time) : null,
  item.employer ?? "", item.role ?? "", item.location ?? "", item.work_arrangement ?? "",
  item.source ?? "", item.ats ?? "", item.job_url ?? "",
  item.posted_at ? new Date(item.posted_at) : null,
  item.days_open == null ? null : Number(item.days_open),
  item.resume_version ?? "",
  item.cover_letter_version ?? "", item.category ?? "", item.current_status ?? "",
  item.confirmation_id ?? "", item.follow_up_date ? new Date(item.follow_up_date) : null,
  item.model_usage == null ? null : Number(item.model_usage), item.archive_id ?? "", item.archive_path ?? "",
]);

// Jobs kept for later. A separate sheet because every column above describes what happened
// after something was sent, and a kept job has no after. A job that has since been applied
// to says so here and keeps its full row on the Applications sheet, so the two add up
// without counting it twice.
const savedHeaders = [
  "Saved Time", "Employer", "Role", "Location", "Work Arrangement", "Source", "ATS",
  "Job URL", "Posted", "Days Open", "Deadline", "Current Status", "Applied", "Evidence",
  "Outcome", "Outcome Recorded", "Verdict", "Direction", "Covered", "Stated",
  "Suggested", "Followed", "Reason",
];
const savedRows = (source.saved_jobs ?? []).map((item) => [
  item.saved_time ? new Date(item.saved_time) : null,
  item.employer ?? "", item.role ?? "", item.location ?? "", item.work_arrangement ?? "",
  item.source ?? "", item.ats ?? "", item.job_url ?? "",
  item.posted_at ? new Date(item.posted_at) : null,
  item.days_open == null ? null : Number(item.days_open),
  // Blank means the employer stated no deadline, never that there is none.
  item.deadline ? new Date(item.deadline) : null,
  item.current_status ?? "",
  item.applied_at ? new Date(item.applied_at) : null,
  // "self-reported" or "tracked application". `application_core`'s `submitted` requires
  // positive submission evidence; saying you applied is not that, and the column says so
  // rather than letting the two claims read alike.
  item.applied_evidence ?? "",
  item.outcome ?? "",
  item.outcome_at ? new Date(item.outcome_at) : null,
  // The call as it was shown, never recomputed: a reply has to be weighable against what
  // was said before it, and by then the directions and the ontology will have moved.
  item.verdict ?? "", item.direction ?? "",
  item.covered == null ? null : Number(item.covered),
  item.stated == null ? null : Number(item.stated),
  item.suggested_choice ?? "",
  item.followed_suggestion == null ? "" : (item.followed_suggestion ? "yes" : "no"),
  item.reason ?? "",
]);

// Column letters are derived from the header list rather than written in. Every range in
// this file used to end at Q, so adding a column silently truncated the sheet.
const columnLetter = (index) => {
  let letter = "";
  for (let value = index; value > 0; value = Math.floor((value - 1) / 26)) {
    letter = String.fromCharCode(65 + ((value - 1) % 26)) + letter;
  }
  return letter;
};

const workbook = Workbook.create();

function buildSheet({ name, title, subtitle, headers: heads, rows: body, widths,
                      dateColumns = [], dateTimeColumns = [], numberColumns = [], tableName }) {
  const last = columnLetter(heads.length);
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  sheet.getRange(`A1:${last}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${last}1`).format = {
    fill: "#12372A",
    font: { bold: true, color: "#FFFFFF", size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${last}1`).format.rowHeight = 30;
  sheet.getRange(`A2:${last}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${last}2`).format = {
    fill: "#E8F1EC",
    font: { color: "#355E4B", italic: true },
    verticalAlignment: "center",
  };
  sheet.getRange(`A3:${last}3`).values = [heads];
  sheet.getRange(`A3:${last}3`).format = {
    fill: "#2F6B50",
    font: { bold: true, color: "#FFFFFF" },
    wrapText: true,
    verticalAlignment: "center",
    borders: { bottom: { style: "medium", color: "#12372A" } },
  };
  sheet.getRange(`A3:${last}3`).format.rowHeight = 34;

  if (body.length > 0) {
    const endRow = 3 + body.length;
    sheet.getRange(`A4:${last}${endRow}`).values = body;
    sheet.getRange(`A4:${last}${endRow}`).format = {
      verticalAlignment: "top",
      borders: { insideHorizontal: { style: "thin", color: "#D9E4DD" } },
    };
    for (const column of dateTimeColumns) {
      sheet.getRange(`${column}4:${column}${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
    }
    for (const column of dateColumns) {
      sheet.getRange(`${column}4:${column}${endRow}`).format.numberFormat = "yyyy-mm-dd";
    }
    for (const column of numberColumns) {
      sheet.getRange(`${column}4:${column}${endRow}`).format.numberFormat = "#,##0";
    }
    const table = sheet.tables.add(`A3:${last}${endRow}`, true, tableName);
    table.style = "TableStyleMedium4";
    table.showBandedRows = true;
    table.showFilterButton = true;
  }

  const height = Math.max(4, body.length + 3);
  for (let column = 0; column < widths.length; column += 1) {
    sheet.getRangeByIndexes(0, column, height, 1).format.columnWidth = widths[column];
  }
  sheet.getRange(`B4:${last}${height}`).format.wrapText = true;
  sheet.freezePanes.freezeRows(3);
  return { sheet, last, height };
}

const stateTime = source.generated_from_state_at ?? "No archived submissions yet";
const applications = buildSheet({
  name: "Applications",
  title: "Jobloom Application Archive",
  subtitle: `Generated from local backend state: ${stateTime}`,
  headers, rows, tableName: "ApplicationsTable",
  widths: [20, 20, 25, 20, 17, 15, 15, 34, 14, 11, 23, 23, 12, 18, 20, 16, 12, 24, 40],
  dateTimeColumns: ["A"], dateColumns: ["I", "P"], numberColumns: ["J", "Q"],
});

buildSheet({
  name: "Saved Jobs",
  title: "Jobloom Saved Jobs",
  subtitle: "Jobs kept to come back to. A job applied to since is marked here and keeps its "
    + "full row on the Applications sheet, so the two never count it twice. Skipping a job "
    + "leaves no record, so this counts jobs kept, never jobs seen.",
  headers: savedHeaders, rows: savedRows, tableName: "SavedJobsTable",
  widths: [20, 20, 25, 20, 17, 15, 15, 34, 14, 11, 14, 16, 20, 18, 20, 20,
           12, 26, 10, 10, 12, 10, 40],
  dateTimeColumns: ["A", "M", "P"], dateColumns: ["I", "K"],
  numberColumns: ["J", "S", "T"],
});

const { last: appLast, height: appHeight } = applications;

const tableCheck = await workbook.inspect({
  kind: "table",
  range: `Applications!A1:${appLast}${appHeight}`,
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: headers.length,
});
const errorCheck = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "tracker formula error scan",
});
const preview = await workbook.render({
  sheetName: "Applications",
  range: `A1:${appLast}${Math.max(4, Math.min(rows.length + 3, 12))}`,
  scale: 1,
  format: "png",
});
if (args.preview) {
  await fs.mkdir(path.dirname(args.preview), { recursive: true });
  await fs.writeFile(args.preview, new Uint8Array(await preview.arrayBuffer()));
  await fs.chmod(args.preview, 0o600);
}
await fs.mkdir(path.dirname(args.output), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(args.output);
await fs.chmod(args.output, 0o600);
await fs.unlink(`${args.output}.inspect.ndjson`).catch((error) => {
  if (error.code !== "ENOENT") throw error;
});
console.log(JSON.stringify({
  status: "written",
  output: args.output,
  preview: args.preview ?? null,
  row_count: rows.length,
  saved_row_count: savedRows.length,
  inspect: tableCheck.ndjson,
  errors: errorCheck.ndjson,
}));
