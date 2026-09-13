import { describe, expect, it } from "vitest";

import { getFileTypeFamily } from "./fileTypes";

describe("file type families", () => {
  it.each([
    ["report.DOCX", "document"],
    [".doc", "document"],
    ["odt", "document"],
    ["rtf", "document"],
    ["XLSX", "spreadsheet"],
    ["xls", "spreadsheet"],
    ["ods", "spreadsheet"],
    ["csv", "spreadsheet"],
    ["tsv", "spreadsheet"],
    ["pptx", "presentation"],
    ["ppt", "presentation"],
    ["odp", "presentation"],
    ["markdown", "markdown"],
    ["PDF", "pdf"],
    ["txt", "text"],
    ["json", "code"],
    ["yaml", "code"],
    ["yml", "code"],
    ["xml", "code"],
    ["html", "web"],
    ["unrecognized", "file"],
    ["", "file"],
  ])("maps %s to %s", (extension, family) => {
    expect(getFileTypeFamily(extension)).toBe(family);
  });
});
