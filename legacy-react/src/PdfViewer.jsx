import React, {useEffect, useRef, useState} from 'react';
import {getDocument, GlobalWorkerOptions} from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import {IconChevronLeft, IconChevronRight, IconMinus, IconPlus, IconExternalLink, IconLoader2, IconAlertTriangle} from '@tabler/icons-react';
import './pdf-viewer.css';

GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({url, pageNumber = 1, onPageChange}) {
  const [pdf, setPdf] = useState(null), [error, setError] = useState('');
  const [loading, setLoading] = useState(true), [rendering, setRendering] = useState(false);
  const [page, setPage] = useState(Number(pageNumber) || 1), [input, setInput] = useState(String(pageNumber || 1));
  const [zoom, setZoom] = useState(1), [width, setWidth] = useState(550);
  const [description, setDescription] = useState('');
  const viewportRef = useRef(null), canvasRef = useRef(null);

  useEffect(() => {
    const observer = new ResizeObserver(entries => setWidth(Math.max(180, entries[0].contentRect.width - 28)));
    if (viewportRef.current) observer.observe(viewportRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setPage(Number(pageNumber) || 1);setInput(String(pageNumber || 1));
  }, [pageNumber, url]);

  useEffect(() => {
    let active = true;
    setPdf(null);setError('');setLoading(true);setDescription('');setZoom(1);
    if (!url) {setLoading(false);return;}
    const task = getDocument({url, cMapUrl:'/pdfjs/cmaps/', cMapPacked:true,
      standardFontDataUrl:'/pdfjs/standard_fonts/', wasmUrl:'/pdfjs/wasm/',
      isEvalSupported:false, enableXfa:false, useSystemFonts:true});
    task.promise.then(document => {if(active) {setPdf(document);setLoading(false);}})
      .catch(failure => {if(active) {setError(failure.name==='PasswordException' ? '这份 PDF 有密码保护，请先上传可阅读的副本。' : 'PDF 暂时无法显示，请检查文件或打开原件查看。');setLoading(false);}});
    return () => {active=false;task.destroy().catch(()=>{});};
  }, [url]);

  useEffect(() => {
    if (!pdf) return;
    let active = true, renderTask;
    setError('');setDescription('');
    if (!Number.isInteger(page) || page < 1 || page > pdf.numPages) {
      setError(`对应页码 ${page} 超出 PDF 的 1–${pdf.numPages} 页范围，请调整页码偏移或输入正确页码。`);
      setRendering(false);return;
    }
    setRendering(true);
    const draw = async () => {
      try {
        const pdfPage = await pdf.getPage(page);
        if (!active) return;
        const base = pdfPage.getViewport({scale:1});
        const scale = Math.min(width / base.width, 1.65) * zoom;
        const view = pdfPage.getViewport({scale});
        const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
        const buffer = window.document.createElement('canvas');
        buffer.width = Math.ceil(view.width * pixelRatio);buffer.height = Math.ceil(view.height * pixelRatio);
        renderTask = pdfPage.render({canvasContext:buffer.getContext('2d'),viewport:view,
          transform:pixelRatio===1?undefined:[pixelRatio,0,0,pixelRatio,0,0]});
        await renderTask.promise;
        if (!active || !canvasRef.current) return;
        const canvas = canvasRef.current;
        canvas.width=buffer.width;canvas.height=buffer.height;
        canvas.style.width=`${Math.round(view.width)}px`;canvas.style.height=`${Math.round(view.height)}px`;
        canvas.getContext('2d').drawImage(buffer,0,0);
        setDescription(`PDF 第 ${page} 页，共 ${pdf.numPages} 页。`);
        setRendering(false);
      } catch (failure) {
        if(active && failure.name!=='RenderingCancelledException') {setError('本页渲染失败，可切换页面重试或打开原始 PDF。');setRendering(false);}
      }
    };
    draw();
    return () => {active=false;renderTask?.cancel();};
  }, [pdf, page, width, zoom]);

  function changePage(next) {
    if(!pdf || !Number.isInteger(next) || next<1 || next>pdf.numPages) {setInput(String(page));return;}
    setPage(next);setInput(String(next));onPageChange?.(next);
  }
  const inRange = !!pdf && page>=1 && page<=pdf.numPages;
  return <div className="pdf-reader">
    <div className="pdf-reader-toolbar">
      <div className="pdf-reader-pages">
        <button aria-label="PDF 上一页" title="上一页" disabled={!pdf || page<=1} onClick={()=>changePage(Math.min(page-1,pdf.numPages))}><IconChevronLeft size={17}/></button>
        <label><span>第</span><input aria-label="PDF 页码" inputMode="numeric" value={input} disabled={!pdf}
          onChange={event=>setInput(event.target.value)} onBlur={()=>changePage(Number(input))}
          onKeyDown={event=>{if(event.key==='Enter') changePage(Number(input));}}/><span>/ {pdf?.numPages || '—'} 页</span></label>
        <button aria-label="PDF 下一页" title="下一页" disabled={!pdf || page>=pdf.numPages} onClick={()=>changePage(Math.max(page+1,1))}><IconChevronRight size={17}/></button>
      </div>
      <div className="pdf-reader-zoom"><button aria-label="缩小 PDF" disabled={zoom<=.75} onClick={()=>setZoom(z=>Math.max(.75,z-.25))}><IconMinus size={15}/></button><button className="pdf-fit" onClick={()=>setZoom(1)} title="恢复适合宽度">{zoom===1?'适合宽度':`${Math.round(zoom*100)}%`}</button><button aria-label="放大 PDF" disabled={zoom>=2} onClick={()=>setZoom(z=>Math.min(2,z+.25))}><IconPlus size={15}/></button></div>
      <a href={`${url}#page=${inRange?page:1}`} target="_blank" rel="noreferrer" aria-label="打开原始 PDF" title="打开原始 PDF"><IconExternalLink size={17}/></a>
    </div>
    <div className="pdf-reader-viewport" ref={viewportRef} aria-busy={loading || rendering}>
      {(loading || rendering) && <div className="pdf-reader-loading" role="status"><IconLoader2 size={19}/>正在显示原始 PDF…</div>}
      {error && <div className="pdf-reader-error" role="alert"><IconAlertTriangle size={22}/><p>{error}</p><a href={url} target="_blank" rel="noreferrer">打开原始 PDF</a></div>}
      <canvas ref={canvasRef} className={loading || rendering || error?'pdf-canvas hidden':'pdf-canvas'} role="img" aria-label={description || '原始 PDF 页面'}/>
    </div>
    <div className="pdf-reader-footer"><span>{description || (loading?'正在读取原文件':'原始 PDF')}</span><span>原件保留 · 可放大核对</span></div>
  </div>;
}
