import { expect, it } from "vitest";
import { parseChunkCsv } from "./chunkCsv";
it("reads BOM headers, commas, escaped quotes and multiline content", () => {
  expect(
    parseChunkCsv(
      '\ufefftitle,content\r\none,"Hello, ""reader""\nNext line"\r\ntwo,Second chunk\r\n',
    ),
  ).toEqual(['Hello, "reader"\nNext line', "Second chunk"]);
});
it.each([
  "wrong\ntext",
  'content\n"unfinished',
  'content\n"quoted"garbage',
  "content,title\nmissing field",
])("rejects invalid CSV: %s", (value) => {
  expect(() => parseChunkCsv(value)).toThrow();
});
