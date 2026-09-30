import {
  CodeFilled,
  FileFilled,
  FileExcelFilled,
  FileMarkdownFilled,
  FilePdfFilled,
  FileTextFilled,
  FileWordFilled,
  FilePptFilled,
  Html5Filled,
} from "@ant-design/icons";

import { getFileTypeFamily, type FileTypeFamily } from "./fileTypes";

const FAMILY_ICONS = {
  pdf: FilePdfFilled,
  document: FileWordFilled,
  spreadsheet: FileExcelFilled,
  presentation: FilePptFilled,
  markdown: FileMarkdownFilled,
  text: FileTextFilled,
  code: CodeFilled,
  web: Html5Filled,
  file: FileFilled,
} satisfies Record<FileTypeFamily, typeof FileFilled>;

export function FileTypeIcon({ type }: { type: string }) {
  const family = getFileTypeFamily(type);
  const Icon = FAMILY_ICONS[family];
  return (
    <span className="tap-file-type" data-family={family} aria-hidden="true">
      <Icon />
    </span>
  );
}
