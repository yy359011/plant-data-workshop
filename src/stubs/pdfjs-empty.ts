// 空 stub：阻止 Vite 对 pdfjs-dist 做 ESM 转换/预构建。
// 真正的 pdf.js 主库和 worker 通过 index.html 的 UMD <script> 标签加载。
export default {};
export const getDocument = () => Promise.reject(new Error('pdfjs-dist not available — use global pdfjsLib via UMD script'));
export const GlobalWorkerOptions = { workerSrc: '' };
export const PDFWorker = class {};
