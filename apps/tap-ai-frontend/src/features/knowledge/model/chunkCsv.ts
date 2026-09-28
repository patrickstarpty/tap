/** Read a CSV content column without changing embedded commas or line breaks. */
export function parseChunkCsv(input: string): string[] {
  const text = input.replace(/^\ufeff/u, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let closed = false;
  const pushField = () => {
    row.push(field);
    field = "";
    closed = false;
  };
  const pushRow = () => {
    pushField();
    if (row.some((value) => value.trim())) rows.push(row);
    row = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i]!;
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          quoted = false;
          closed = true;
        }
      } else field += c;
      continue;
    }
    if (c === ",") {
      pushField();
      continue;
    }
    if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      pushRow();
      continue;
    }
    if (closed) throw new Error("CSV 引号结束后只能跟逗号或换行。");
    if (c === '"') {
      if (field.length) throw new Error("CSV 引号必须位于字段开头。");
      quoted = true;
    } else field += c;
  }
  if (quoted) throw new Error("CSV 引号未闭合，请检查文件。");
  pushRow();
  const headers = rows[0] ?? [];
  const column = headers.findIndex(
    (value) => value.trim().toLowerCase() === "content",
  );
  if (column < 0) throw new Error("CSV 必须包含 content 列。");
  if (rows.slice(1).some((values) => values.length !== headers.length))
    throw new Error("CSV 行的列数不一致，请检查文件。");
  const contents = rows
    .slice(1)
    .map((values) => values[column]!.trim())
    .filter(Boolean);
  if (!contents.length) throw new Error("CSV 的 content 列没有可导入内容。");
  if (
    contents.length > 1000 ||
    contents.some((value) => Array.from(value).length > 32768)
  )
    throw new Error("最多导入 1000 个切片，每个不超过 32768 字符。");
  return contents;
}
export function readChunkCsv(file: File): Promise<string[]> {
  if (file.size > 25 * 1024 * 1024)
    return Promise.reject(new Error("CSV 文件不能超过 25 MB。"));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("CSV 读取失败，请重新选择文件。"));
    reader.onload = () => {
      try {
        resolve(parseChunkCsv(String(reader.result)));
      } catch (error) {
        reject(error);
      }
    };
    reader.readAsText(file);
  });
}
