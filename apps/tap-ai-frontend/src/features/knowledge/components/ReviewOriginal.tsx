import { useEffect, useRef, useState } from "react";
import { Alert, Button, Space } from "antd";

export function ReviewOriginal({
  reviewId,
  itemId,
  locator,
  extractedText,
  load,
}: {
  reviewId: string;
  itemId: string;
  locator: string;
  extractedText?: string;
  load: (reviewId: string, itemId: string) => Promise<Blob>;
}) {
  const [source, setSource] = useState<{
    url: string;
    type: string;
    key: string;
  } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const generation = useRef(0);
  const sourceUrl = useRef<string | null>(null);
  const key = `${reviewId}/${itemId}`;
  useEffect(() => {
    ++generation.current;
    return () => {
      ++generation.current;
      if (sourceUrl.current) URL.revokeObjectURL(sourceUrl.current);
      sourceUrl.current = null;
    };
  }, [key]);
  const current = source?.key === key ? source : null;
  const pageMatch = /^page:([1-9]\d*)(?:$|\/)/u.exec(locator);
  const page = pageMatch ? Number(pageMatch[1]) : null;
  const open = async () => {
    const request = ++generation.current;
    setPending(true);
    setError(false);
    try {
      const blob = await load(reviewId, itemId);
      if (request !== generation.current) return;
      if (sourceUrl.current) URL.revokeObjectURL(sourceUrl.current);
      const url = URL.createObjectURL(blob);
      sourceUrl.current = url;
      setSource({ url, type: blob.type, key });
    } catch {
      if (request === generation.current) setError(true);
    } finally {
      if (request === generation.current) setPending(false);
    }
  };
  const extension =
    current?.type === "application/pdf"
      ? "pdf"
      : current?.type.includes("spreadsheetml")
        ? "xlsx"
        : current?.type.includes("wordprocessingml")
          ? "docx"
          : "txt";
  return (
    <section aria-label="原文件阅读" className="tapper-original-reader">
      <Space wrap>
        <Button loading={pending} onClick={() => void open()}>
          查看原文件
        </Button>
        {current ? (
          <a href={current.url} download={`source.${extension}`}>
            下载原文件
          </a>
        ) : null}
      </Space>
      <p>原件位置：{locator}。请对照当前版本，在下方记录核对意见。</p>
      {error ? (
        <Alert type="error" title="原文件读取失败，请检查权限后重试。" />
      ) : null}
      {current?.type === "application/pdf" ? (
        <>
          <iframe
            title={page ? `原 PDF · 第 ${page} 页` : "原 PDF"}
            src={page ? `${current.url}#page=${page}` : current.url}
            style={{
              width: "100%",
              height: 520,
              border: "1px solid var(--tap-line)",
              borderRadius: 8,
            }}
          />
          <p>
            若浏览器无法显示 PDF，请下载原文件
            {page ? `并转到第 ${page} 页` : ""}核对。
          </p>
        </>
      ) : current?.type.includes("spreadsheetml") ? (
        <>
          {extractedText?.includes("\t") ? (
            <div className="tapper-original-grid-scroll">
              <table
                aria-label="工作表提取预览"
                className="tapper-original-grid"
              >
                <thead>
                  <tr>
                    {extractedText
                      .split("\n")[0]!
                      .split("\t")
                      .slice(0, 32)
                      .map((cell, index) => (
                        <th key={index} scope="col">
                          {cell || `列 ${index + 1}`}
                        </th>
                      ))}
                  </tr>
                </thead>
                <tbody>
                  {extractedText
                    .split("\n")
                    .slice(1, 21)
                    .map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row
                          .split("\t")
                          .slice(0, 32)
                          .map((cell, columnIndex) => (
                            <td key={columnIndex}>{cell || "—"}</td>
                          ))}
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p>
            表格为提取结果预览；请下载原文件按上述单元格位置核对公式、格式、隐藏行列与合并单元格。
          </p>
        </>
      ) : current ? (
        <p>请下载原文件按上述位置核对。</p>
      ) : null}
    </section>
  );
}
