// Shared visual classification, not a backend ingestion allowlist.
export const FILE_TYPE_EXTENSIONS = {
  pdf: ["PDF"],
  document: ["DOC", "DOCX", "ODT", "RTF"],
  spreadsheet: ["XLS", "XLSX", "ODS", "CSV", "TSV"],
  presentation: ["PPT", "PPTX", "ODP"],
  markdown: ["MD", "MARKDOWN"],
  text: ["TXT", "LOG"],
  code: ["JSON", "YAML", "YML", "XML", "TS"],
  web: ["HTML", "HTM"],
  file: [],
} as const;

export type FileTypeFamily = keyof typeof FILE_TYPE_EXTENSIONS;

export function getFileTypeFamily(typeOrFilename: string): FileTypeFamily {
  const extension = typeOrFilename.trim().split(".").pop()?.toUpperCase() ?? "";
  for (const [family, extensions] of Object.entries(FILE_TYPE_EXTENSIONS)) {
    if ((extensions as readonly string[]).includes(extension)) {
      return family as FileTypeFamily;
    }
  }
  return "file";
}
