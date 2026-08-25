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

const headers = [
  "Submission Time", "Employer", "Role", "Location", "Work Arrangement", "Source", "ATS",
  "Application URL", "Resume Version", "Cover Letter Version", "Category", "Current Status",
  "Confirmation ID", "Follow-up Date", "Model Usage", "Archive ID", "Archive Path",
];
const rows = source.applications.map((item) => [
  item.submission_time ? new Date(item.submission_time) : null,
  item.employer ?? "", item.role ?? "", item.location ?? "", item.work_arrangement ?? "",
  item.source ?? "", item.ats ?? "", item.application_url ?? "", item.resume_version ?? "",
  item.cover_letter_version ?? "", item.category ?? "", item.current_status ?? "",
  item.confirmation_id ?? "", item.follow_up_date ? new Date(item.follow_up_date) : null,
  item.model_usage == null ? null : Number(item.model_usage), item.archive_id ?? "", item.archive_path ?? "",
]);

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Applications");
sheet.showGridLines = false;
sheet.getRange("A1:Q1").merge();
sheet.getRange("A1").values = [["Jobloom Application Archive"]];
sheet.getRange("A1:Q1").format = {
  fill: "#12372A",
  font: { bold: true, color: "#FFFFFF", size: 16 },
  verticalAlignment: "center",
};
sheet.getRange("A1:Q1").format.rowHeight = 30;
sheet.getRange("A2:Q2").merge();
const stateTime = source.generated_from_state_at ?? "No archived submissions yet";
sheet.getRange("A2").values = [[`Generated from local backend state: ${stateTime}`]];
sheet.getRange("A2:Q2").format = {
  fill: "#E8F1EC",
  font: { color: "#355E4B", italic: true },
  verticalAlignment: "center",
};
sheet.getRange("A3:Q3").values = [headers];
sheet.getRange("A3:Q3").format = {
  fill: "#2F6B50",
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
  verticalAlignment: "center",
  borders: { bottom: { style: "medium", color: "#12372A" } },
};
sheet.getRange("A3:Q3").format.rowHeight = 34;

if (rows.length > 0) {
  const endRow = 3 + rows.length;
  sheet.getRange(`A4:Q${endRow}`).values = rows;
  sheet.getRange(`A4:Q${endRow}`).format = {
    verticalAlignment: "top",
    borders: { insideHorizontal: { style: "thin", color: "#D9E4DD" } },
  };
  sheet.getRange(`A4:A${endRow}`).format.numberFormat = "yyyy-mm-dd hh:mm";
  sheet.getRange(`N4:N${endRow}`).format.numberFormat = "yyyy-mm-dd";
  sheet.getRange(`O4:O${endRow}`).format.numberFormat = "#,##0";
  const table = sheet.tables.add(`A3:Q${endRow}`, true, "ApplicationsTable");
  table.style = "TableStyleMedium4";
  table.showBandedRows = true;
  table.showFilterButton = true;
}

const widths = [20, 20, 25, 20, 17, 15, 15, 34, 23, 23, 12, 18, 20, 16, 12, 24, 40];
for (let column = 0; column < widths.length; column += 1) {
  sheet.getRangeByIndexes(0, column, Math.max(4, rows.length + 3), 1).format.columnWidth = widths[column];
}
sheet.getRange(`B4:Q${Math.max(4, rows.length + 3)}`).format.wrapText = true;
sheet.freezePanes.freezeRows(3);

const tableCheck = await workbook.inspect({
  kind: "table",
  range: `Applications!A1:Q${Math.max(4, rows.length + 3)}`,
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 17,
});
const errorCheck = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "tracker formula error scan",
});
const preview = await workbook.render({
  sheetName: "Applications",
  range: `A1:Q${Math.max(4, Math.min(rows.length + 3, 12))}`,
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
  inspect: tableCheck.ndjson,
  errors: errorCheck.ndjson,
}));
