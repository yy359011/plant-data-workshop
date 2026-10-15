import React, {useCallback, useEffect, useRef, useState} from 'react';
import {IconFolder, IconUpload, IconFileText, IconPhoto, IconListDetails, IconSettings, IconDatabase, IconBook2, IconShieldCheck, IconArrowRight, IconChevronLeft, IconChevronRight, IconPlus, IconX, IconDownload, IconExternalLink, IconFlask, IconCheck, IconAlertTriangle, IconLoader2, IconTable, IconTrash, IconLock, IconDeviceDesktop} from '@tabler/icons-react';
import {PdfViewer} from './PdfViewer';
import './chapter-workshop.css';
import {BatchReviewBar} from './BatchReviewBar.jsx';

const documentTitle = value => String(value || '未命名文献').replace(/[（(](?:真实论文|章节管线合成示例|合成演示|测试文献)[）)]/g,'').trim();
const Icon = ({as: Component, size = 18, ...props}) => <Component size={size} stroke={1.7} {...props}/>;
const statusNames = {pending:'待加工',ready:'已编排',queued:'排队中',processing:'加工中',completed:'待复核',review:'待复核',approved:'已通过',rejected:'不采用',error:'加工失败',materialized:'已送入下游'};
const purposeNames = {both:'训练 + 知识库',training:'仅训练数据',kb:'仅知识库'};
const licenseNames = {self_owned:'自有 / 已获授权',research_only:'仅内部知识库',unknown:'用途待核对'};
const blank = {documents:[],jobs:[],model:{endpoint:'',model:'',vision:false,configured:false,has_key:false}};
const byteSize = n => n >= 1048576 ? `${(n / 1048576).toFixed(1)} MiB` : `${Math.max(1,Math.ceil(n / 1024))} KiB`;
const dateLabel = value => value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : '—';
const encode = file => new Promise((resolve,reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(Error(`无法读取 ${file.name}`)); reader.readAsDataURL(file); });
async function request(path, body) {
  const response = await fetch('/api/chapters' + path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,...(new URLSearchParams(location.hash.split('?')[1]||'').get('batch')?{batch_id:new URLSearchParams(location.hash.split('?')[1]||'').get('batch')}:{})})});
  const value = await response.json().catch(() => ({error:'文献加工服务未返回有效结果，请检查本地服务。'}));
  if (!response.ok) {if(response.status===401)window.dispatchEvent(new Event('platform-session-expired'));const failure=Error(value.error || '操作未完成，请重试。');failure.status=response.status;throw failure;}
  return value;
}
const Badge = ({status,children}) => <span className={`cw-badge ${status || ''}`}>{children || statusNames[status] || status}</span>;
const Button = ({icon,children,variant='',...props}) => <button className={`cw-button ${variant}`} {...props}>{icon && <Icon as={icon}/>}<span>{children}</span></button>;
const interpretationOf = result => typeof result?.interpretation==='string'?{text:result.interpretation,source_pages:[]}:{text:result?.interpretation?.text || '',source_pages:[...(result?.interpretation?.source_pages || [])]};
const draftValues = editor => ({corpus:editor.text || '',interpretation:editor.interpretation || {text:'',source_pages:[]},structured_fields:editor.fields || []});
const draftSignature = editor => JSON.stringify(draftValues(editor));
function draftProblems(editor,schema,pages) {
  const issues=[], pageNumbers=new Set(pages.map(page=>page.number)), definitions=new Map(schema.map(field=>[field.key,field]));
  if(editor.interpretation?.text.trim() && !editor.interpretation.source_pages?.length) issues.push('独立解读需要选择出处页。');
  for(const field of editor.fields || []) {
    if(field.status!=='found') continue;
    const definition=definitions.get(field.key), label=definition?.label || field.key;
    if(field.value==null || field.value==='' || definition?.type==='number' && (typeof field.value!=='number' || !Number.isFinite(field.value)) || definition?.type==='boolean' && typeof field.value!=='boolean') issues.push(`${label}：填写符合字段类型的值。`);
    if(!field.evidence?.trim()) issues.push(`${label}：请粘贴原文证据。`);
    if(!field.source_pages?.length || field.source_pages.some(number=>!pageNumbers.has(number))) issues.push(`${label}：请选择本章的来源页。`);
  }
  return issues;
}
function editorFrom(chapter) {
  const result=chapter?.result || {}, text=chapter?.corpus || result.corpus || '';
  const value={chapterId:chapter?.id || '',revision:chapter?.revision ?? 0,text,baselineText:text,interpretation:interpretationOf(result),fields:JSON.parse(JSON.stringify(result.structured_fields || [])),conflict:false};
  return {...value,baseline:draftSignature(value)};
}
function resolveMarkdownImage(source,images) {
  const raw=String(source || '').replace(/^<|>$/g,'');
  const byId=images.find(image=>raw===image.id || raw===`image:${image.id}` || raw===`image://${image.id}`);
  if(byId) return byId;
  if(/[\\\u0000-\u001f]/.test(raw)) return null;
  if(/^(?:https?:)?\/\//i.test(raw) || raw.startsWith('/')) {
    try {const url=new URL(raw,location.origin);if(url.origin!==location.origin || url.search || url.hash) return null;return images.find(image=>image.url===url.pathname) || null;} catch {return null;}
  }
  const path=raw.replace(/^\.\//,'');
  if(path.split('/').some(part=>part==='..') || /%2e|%2f|%5c/i.test(path)) return null;
  return images.find(image=>path===image.path?.replace(/^\.\//,'')) || null;
}
function PageMarkdown({text,images=[],pdfs=[],onPageLink,onImage}) {
  const lines = String(text || '本页没有可读取的文字。').replace(/\r\n?/g,'\n').split('\n'), blocks = [];
  function inline(value) {
    const input=String(value || ''), pattern=/(!?)\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)|\*\*([^*]+)\*\*|`([^`]+)`/g, output=[];
    let cursor=0, match;
    while((match=pattern.exec(input))) {
      if(match.index>cursor) output.push(input.slice(cursor,match.index));
      const key=`${match.index}-${cursor}`;
      if(match[4]) output.push(<strong key={key}>{match[4]}</strong>);
      else if(match[5]) output.push(<code key={key}>{match[5]}</code>);
      else {
        const [,isImage,label,source]=match, image=resolveMarkdownImage(source,images), pageMatch=source.match(/^#(?:page|source-page)=(\d+)$/);
        if(isImage) output.push(image?<span className="cw-md-image" key={key}><button type="button" onClick={()=>onImage?.(image)} aria-label={`查看原始图片 ${label || image.caption || image.path}`}><img src={image.url} alt={label || image.caption || '原始文献图片'} loading="lazy"/></button><small>{label || image.caption || image.path}</small></span>:<span className="cw-md-blocked" key={key}>[图片未匹配到本章原件：{label || source}]</span>);
        else if(pageMatch && onPageLink) output.push(<button className="cw-source-link" type="button" key={key} onClick={()=>onPageLink(Number(pageMatch[1]))}>{label || `第 ${pageMatch[1]} 页`}</button>);
        else if(image) output.push(<button className="cw-source-link" type="button" key={key} onClick={()=>onImage?.(image)}>{label || '查看原图'}</button>);
        else {
          let href=null;
          try {const url=new URL(source,location.origin);if(/^https?:$/.test(url.protocol) && (url.origin!==location.origin || pdfs.some(pdf=>pdf.url===url.pathname && !url.search && !url.hash))) href=url.href;} catch {}
          output.push(href?<a key={key} href={href} target="_blank" rel="noopener noreferrer">{label || source}<Icon as={IconExternalLink} size={12}/></a>:<span className="cw-md-blocked-link" key={key}>{label || source}</span>);
        }
      }
      cursor=pattern.lastIndex;
    }
    if(cursor<input.length) output.push(input.slice(cursor));
    return output.length?output:input;
  }
  const cells = line => line.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(cell=>cell.trim().replace(/\\\|/g,'|'));
  const separator = line => line?.includes('|') && cells(line).every(cell=>/^:?-{3,}:?$/.test(cell));
  const tableStart = index => lines[index]?.includes('|') && separator(lines[index+1]);
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {index++;continue;}
    if (tableStart(index)) {
      const header = cells(line), rows = [];index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {rows.push(cells(lines[index++]));}
      blocks.push(<div className="cw-page-table-wrap" key={`table-${index}`}><table className="cw-page-table"><thead><tr>{header.map((cell,i)=><th key={i}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row,i)=><tr key={i}>{header.map((_,j)=><td key={j}>{inline(row[j] || '')}</td>)}</tr>)}</tbody></table></div>);
      continue;
    }
    if (/^#{1,6}\s/.test(line)) {blocks.push(<h3 key={index}>{inline(line.replace(/^#+\s*/,''))}</h3>);index++;continue;}
    if (/^\s*```/.test(line)) {
      const start=index++, code=[];while(index<lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);if(index<lines.length) index++;
      blocks.push(<pre className="cw-page-code" key={start}>{code.join('\n')}</pre>);continue;
    }
    if (/^\s*[-*+]\s/.test(line)) {
      const start=index, items=[];while(index<lines.length && /^\s*[-*+]\s/.test(lines[index])) items.push(lines[index++].replace(/^\s*[-*+]\s/,''));
      blocks.push(<ul key={start}>{items.map((item,i)=><li key={i}>{inline(item)}</li>)}</ul>);continue;
    }
    if (/^\s*>/.test(line)) {
      const start=index, quote=[];while(index<lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/,''));
      blocks.push(<blockquote className="cw-page-quote" key={start}>{inline(quote.join('\n'))}</blockquote>);continue;
    }
    const start=index, paragraph=[];
    while(index<lines.length && lines[index].trim() && !tableStart(index) && !/^(?:#{1,6}\s|\s*```|\s*[-*+]\s|\s*>)/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={start}>{inline(paragraph.join('\n'))}</p>);
  }
  return <>{blocks}</>;
}
function Dialog({title,subtitle,onClose,wide=false,children}) {
  const ref = useRef(null);
  useEffect(() => {
    const previous = document.activeElement, overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const controls = () => [...(ref.current?.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]') || [])].filter(el => el.getClientRects().length);
    controls()[0]?.focus();
    const handle = event => { if (event.key === 'Escape') onClose(); if (event.key === 'Tab') {const items = controls(), first = items[0], last = items[items.length - 1];if (event.shiftKey && document.activeElement === first) {event.preventDefault();last?.focus();} else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first?.focus();}} };
    document.addEventListener('keydown',handle);
    return () => { document.body.style.overflow = overflow; document.removeEventListener('keydown',handle); previous?.focus(); };
  }, []);
  return <div className="cw-modal-backdrop" onClick={event => {if(event.target === event.currentTarget) onClose();}}><section className={`cw-modal ${wide?'wide':''}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}><header className="cw-modal-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="cw-icon-button" aria-label="关闭" onClick={onClose}><Icon as={IconX}/></button></header>{children}</section></div>;
}

export function ChapterWorkshop({onChanged,go,setToast,user}) {
  const query = useRef(new URLSearchParams(location.hash.split('?')[1] || ''));
  const documentRef = useRef(query.current.get('document') || ''), requestedPage = useRef(query.current.get('page') || ''), alive = useRef(true), guardAction = useRef(null);
  const [state,setState] = useState(blank), [detail,setDetail] = useState(null), [documentId,setDocumentId] = useState(documentRef.current), [chapterId,setChapterId] = useState(''), [pageId,setPageId] = useState('');
  const [editor,setEditor] = useState(()=>editorFrom(null)), [loading,setLoading] = useState(true), [error,setError] = useState(''), [busy,setBusy] = useState(''), [dialog,setDialog] = useState(null);
  const [sourceTab,setSourceTab] = useState('pdf'), [pdfPage,setPdfPage] = useState(1), [instructions,setInstructions] = useState(''), [resultTab,setResultTab] = useState('sft'), [checksOpen,setChecksOpen] = useState(false), [sftReviewed,setSftReviewed] = useState(false), [success,setSuccess] = useState(null);
  const [productTab,setProductTab]=useState('markdown'), [markdownMode,setMarkdownMode]=useState('preview');
  const batchId=query.current.get('batch'), isAdmin=user?.role==='admin';
  const chapterRef = useRef(query.current.get('chapter')||''), busyRef = useRef(false), refreshSequence=useRef(0);
  const [pendingHandoff,setPendingHandoff] = useState(null);
  const notify = message => setToast?.(message);
  const refresh = useCallback(async (preferred) => {
    const requestId=++refreshSequence.current;
    try {
      const next = await request('/state');if(!alive.current || requestId!==refreshSequence.current) return;
      const normalized={...blank,...next,model:{...blank.model,...next.model}};setState(normalized);
      const candidate=preferred===undefined?documentRef.current:preferred;
      const id=normalized.documents.some(doc=>doc.id===candidate)?candidate:'';
      documentRef.current=id;setDocumentId(id);
      if(!id) {setDetail(null);setChapterId('');setError(candidate?'这篇文献不存在，请返回文献管理重新选择。':'');setLoading(false);return null;}
      const fresh=await request('/documents/'+encodeURIComponent(id));if(!alive.current || requestId!==refreshSequence.current || documentRef.current!==id) return;
      setDetail(fresh);
      const target=requestedPage.current?fresh.pages?.find(page=>page.id===requestedPage.current || String(page.number)===requestedPage.current):null;
      const targetChapter=target?fresh.chapters?.find(ch=>ch.page_ids?.includes(target.id)):null;
      const idChapter=targetChapter?.id || (fresh.chapters?.some(ch=>ch.id===chapterRef.current)?chapterRef.current:fresh.chapters?.[0]?.id || '');
      chapterRef.current=idChapter;setChapterId(idChapter);
      if(target && targetChapter) {setPageId(target.id);requestedPage.current='';}
      setError('');setLoading(false);return fresh;
    } catch(failure) {if(alive.current && requestId===refreshSequence.current) {setError(failure.message);setLoading(false);}return null;}
  },[]);
  useEffect(()=>{alive.current=true;refresh();return()=>{alive.current=false;refreshSequence.current++;};},[refresh]);
  const document=detail?.document, pages=detail?.pages || [], chapters=detail?.chapters || [], pdfs=detail?.pdfs || [];
  const contexts=document?.library_contexts || (document?.library_context?[document.library_context]:[]);
  const libraryContext=contexts.find(context=>context.record_id===query.current.get('record') && (!query.current.get('source') || context.source_id===query.current.get('source'))) || contexts[0];
  const libraryRoute=libraryContext?'library?'+new URLSearchParams({source:libraryContext.source_id,record:libraryContext.record_id}).toString():'library';
  const chapter=chapters.find(ch=>ch.id===chapterId) || chapters[0], result=chapter?.result || {};
  const members=chapter?pages.filter(page=>chapter.page_ids?.includes(page.id)):pages;
  const page=members.find(p=>p.id===pageId) || (document?.is_demo?members.find(p=>p.images?.length):null) || members[0];
  const selectedPdf=pdfs.find(pdf=>pdf.id===document?.selected_pdf_id) || detail?.selected_pdf || pdfs[0];
  const offset=Number(document?.pdf_page_offset || 0), mappedPage=page?Number(page.number)+offset:1;
  const currentJobs=state.jobs.filter(job=>job.document_id===documentId).sort((a,b)=>String(b.created_at || '').localeCompare(String(a.created_at || '')));
  const currentJob=currentJobs.find(job=>['queued','processing'].includes(job.status));
  const processing=!!currentJob, polling=state.jobs.some(job=>['queued','processing'].includes(job.status));
  useEffect(()=>{if(!polling) return;const timer=setInterval(()=>refresh(),2000);return()=>clearInterval(timer);},[polling,refresh]);
  const sourceCorpus=chapter?.corpus || result.corpus || '', revision=chapter?.revision ?? 0;
  const sourceSignature=draftSignature(editorFrom(chapter));
  useEffect(()=>{
    if(!chapter) return;
    setEditor(previous=>{
      const incoming=editorFrom(chapter);
      if(previous.chapterId!==chapter.id) return incoming;
      if(previous.revision===revision && previous.baseline===incoming.baseline) return previous;
      if(draftSignature(previous)!==previous.baseline) return {...previous,conflict:true};
      return incoming;
    });
  },[chapter?.id,revision,sourceSignature]);
  useEffect(()=>{setSftReviewed(false);setChecksOpen(false);},[chapter?.id,revision]);
  useEffect(()=>{setInstructions('');setSuccess(null);setPendingHandoff(null);},[chapter?.id]);
  useEffect(()=>{if(page) setPdfPage(mappedPage);},[page?.id,selectedPdf?.id,offset]);
  const dirty=!!chapter && editor.chapterId===chapter.id && draftSignature(editor)!==editor.baseline, sft=result.sft_samples || [];
  useEffect(()=>{window.dispatchEvent(new CustomEvent('chapter-unsaved',{detail:{dirty}}));const warn=event=>{if(dirty){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>{window.removeEventListener('beforeunload',warn);};},[dirty]);
  useEffect(()=>()=>window.dispatchEvent(new CustomEvent('chapter-unsaved',{detail:{dirty:false}})),[]);
  function pageLabel(values) {if(!Array.isArray(values) || !values.length) return '来源页未标注';return '第 '+values.map(value=>pages.find(p=>p.id===value)?.number || value).join('、')+' 页';}
  function guard(action) {if(dirty){guardAction.current=action;setDialog('unsaved');}else action();}
  function changeChapter(id) {guard(()=>{chapterRef.current=id;setChapterId(id);setPageId('');setSuccess(null);});}
  function changeDocument(id) {guard(()=>{documentRef.current=id;chapterRef.current='';setDetail(null);setLoading(true);setSuccess(null);refresh(id);});}
  function acceptEditor(ch) {if(ch) setEditor(editorFrom(ch));}
  function draftBody() {return {chapter_id:chapter.id,expected_revision:editor.revision,...draftValues(editor)};}
  async function operation(type,fn) {if(busyRef.current) return null;busyRef.current=true;setBusy(type);setError('');try{return await fn();}catch(failure){if(failure.status===409) await refresh();setError(failure.message);notify(failure.message);return null;}finally{busyRef.current=false;setBusy('');}}
  async function saveRevision() {
    if(!dirty) return chapter;
    return operation('save',async()=>{const outcome=await request('/revise',draftBody());const saved=outcome.chapter;acceptEditor(saved);setSuccess(null);setPendingHandoff(null);await refresh();await onChanged?.();notify('图文、解读和字段修改已保存为新的待确认版本；原问答候选已移除。');return saved;});
  }
  async function leaveDraft(save) {const action=guardAction.current;let saved;if(save){saved=await saveRevision();if(!saved) return;}else {acceptEditor(chapter);saved=chapter;}guardAction.current=null;setDialog(null);action?.(saved);}
  async function run(mode,reorganize=false) {
    const visibleRevision=editor.chapterId===chapter.id?editor.revision:chapter.revision;
    guard(saved=>operation('run',async()=>{await request('/run',{document_id:document.id,chapter_ids:[chapter.id],mode,expected_revision:saved?.revision ?? visibleRevision,expected_schema_revision:document.schema_revision || 0,...(reorganize?{instructions:instructions.trim()}:{})});if(reorganize) setInstructions('');await refresh();setSuccess(null);setPendingHandoff(null);notify(mode==='demo'?'合成演示整理已开始。':'AI整理已开始，结果会自动更新。');}));
  }
  async function confirmUse() {
    if(document.license==='unknown' || document.license==='research_only' && document.purpose==='training') {setDialog('metadata');return;}
    await operation('confirm',async()=>{
      let latest={...chapter,revision:editor.revision};
      let approved=latest.status==='approved', stage='save';
      setSuccess(null);
      try {
        if(dirty) {const saved=await request('/revise',draftBody());latest=saved.chapter;approved=false;acceptEditor(latest);}
        stage='review';
        if(!approved) {const outcome=await request('/review',{chapter_id:latest.id,action:'approve',expected_revision:latest.revision,corpus:latest.corpus || latest.result?.corpus});latest={...latest,...outcome,id:latest.id};approved=true;acceptEditor(latest);}
        stage='materialize';
        const materialized=await request('/materialize',{document_id:document.id,chapter_id:latest.id,expected_revision:latest.revision});
        if(!materialized.assets?.length) throw Error('本章未生成可用产出，请检查文献用途与服务日志。');
        await refresh();await onChanged?.();setPendingHandoff(null);setSuccess({chapterId:chapter.id,revision:latest.revision,...materialized});notify('本章已确认并准备好产出。');return materialized;
      } catch(failure) {
        await refresh();
        if(approved && stage==='materialize') {setPendingHandoff({chapterId:latest.id,revision:latest.revision});const message=Error('已确认，送入产出未完成，请重试。'+failure.message);message.status=failure.status;throw message;}
        throw failure;
      }
    });
  }
  async function reject() {const visibleRevision=editor.revision;guard(saved=>operation('reject',async()=>{await request('/review',{chapter_id:chapter.id,action:'reject',expected_revision:saved?.revision ?? visibleRevision});await refresh();await onChanged?.();notify('本章已标记暂不采用。');}));}
  function locatePage(number) {const target=pages.find(p=>p.number===Number(number));if(!target) {notify('引用页不在当前文献中。');return;}const owner=chapters.find(ch=>ch.page_ids?.includes(target.id));const action=()=>{if(owner && owner.id!==chapter?.id){chapterRef.current=owner.id;setChapterId(owner.id);}setPageId(target.id);setPdfPage(Number(target.number)+offset);setSourceTab('pdf');};if(owner && owner.id!==chapter?.id) guard(action);else action();}
  function openImage(image) {setDialog({image,page:pages.find(p=>p.images?.some(img=>img.id===image.id))});}
  async function choosePdf(id) {await operation('pdf',async()=>{await request('/documents/'+encodeURIComponent(document.id),{selected_pdf_id:id});await refresh();});}
  const reviewedCount=chapters.filter(ch=>ch.status==='approved').length, materializedCount=document?.materialized_count || 0;
  const needsSftReview=!dirty && chapter?.status==='review' && sft.length>0 && !sftReviewed;
  const validationIssues=draftProblems(editor,result.extraction_schema || [],members);
  const noPurpose=document?.license==='unknown' || document?.license==='research_only' && document?.purpose==='training';
  const canConfirm=!!sourceCorpus && editor.text.trim() && !validationIssues.length && (dirty || ['review','approved'].includes(chapter?.status)) && !processing && !busy && !editor.conflict && !needsSftReview;
  const nextChapter=chapters[chapters.findIndex(ch=>ch.id===chapter?.id)+1];
  const handoffFailed=!!pendingHandoff && !!chapter && pendingHandoff.chapterId===chapter.id && pendingHandoff.revision===revision && !dirty;
  const handoffReady=!!success && !!chapter && success.chapterId===chapter.id && success.revision===revision && !dirty;
  const outputPurpose=document?.purpose==='kb' || document?.license==='research_only'?'知识库':document?.purpose==='training'?'训练数据':'训练数据或知识库';
  const instruction=loading?'正在载入文献…':!document?'请先从数据源选择文献。':processing?'AI正在整理当前章节。完成后，对照左侧原件确认内容。':dirty?'右侧有未保存修改。可以先保存，也可以直接确认并使用。':!sourceCorpus?(state.model.configured?'文献已导入。点击“整理这一章”，让AI生成可编辑的结果。':'文献已导入。先配置AI服务，再点击“整理这一章”。'):handoffFailed?'这一章已确认，产出尚未准备完成。点击“重试准备产出”。':handoffReady?`这一章已准备好，可到产出中心用于${outputPurpose}。`:chapter.status==='approved'?'这一章已确认。点击“确认并使用”准备当前版本的产出。':chapter.status==='rejected'?'这一章暂不采用。可修改正文或重新整理后再确认。':'请对照左侧原始PDF，检查并修改右侧结果，然后确认使用。';
  const step=!document?1:chapter?.status==='approved' && !dirty?3:2;
  const images=pages.flatMap(p=>p.images || []);
  return <div className="cw-root cw-simple">
    <nav className="cw-library-path" aria-label="文献所在目录"><button onClick={()=>go('library')}>数据源</button>{libraryContext && <><Icon as={IconChevronRight} size={13}/><button onClick={()=>go('library?source='+encodeURIComponent(libraryContext.source_id))}>{libraryContext.source.name}</button><Icon as={IconChevronRight} size={13}/><button onClick={()=>go(libraryRoute)}>{libraryContext.record.name}</button></>}<Icon as={IconChevronRight} size={13}/><span>文献整理</span></nav>
    <div className="cw-simple-heading"><div><h1>文献整理</h1><p>对照原件修订，抽检结论归入文献批次。</p></div><div className="cw-actions"><Button icon={IconChevronLeft} onClick={()=>go(libraryRoute)}>返回文献列表</Button></div></div>
    {document && <div className="cw-document-bar"><Icon as={IconFileText} size={23}/><strong className="cw-document-title">{documentTitle(document.title || document.name)}</strong><span className="cw-document-count">{pages.length} 页 · {chapters.length} 章</span><button className="cw-text-button" onClick={()=>setDialog('metadata')}>用途设置<Icon as={IconChevronRight} size={14}/></button></div>}
    {document?.source_metadata && <SourceMetadata metadata={document.source_metadata}/>}
    <div className="cw-simple-steps" aria-label="整理步骤">{[[1,'导入文献'],[2,'对照整理'],[3,'批次抽检']].map(([number,title])=><React.Fragment key={number}><span className={number===step?'active':number<step?'done':''}><b>{number<step?<Icon as={IconCheck} size={13}/>:number}</b>{title}</span>{number<3 && <Icon as={IconChevronRight} size={14}/>}</React.Fragment>)}<p>{dirty?'右侧有未保存修改，请先保存，再记录抽检结论。':processing?'AI正在整理，完成后可对照原文核查。':batchId?'核对抽中的章节，记录通过或问题，然后返回批次。':'检查章节产物。进入处理与抽检，将文献归入批次后生成数据包。'}</p></div>
    {error && <div className="cw-alert error" role="alert"><Icon as={IconAlertTriangle}/><span>{error}</span><button onClick={()=>refresh()}>重新载入</button></div>}
    {loading?<div className="cw-empty"><Icon as={IconLoader2} size={28} className="cw-spinner"/><strong>正在准备原文与整理结果…</strong></div>:!document?<div className="cw-welcome"><Icon as={IconFolder} size={38}/><h2>先选择数据源和文献</h2><p>进入物种或数据条目后，选择已有文献，也可以导入新的文献资料。</p><div className="cw-actions"><Button variant="primary" icon={IconFolder} onClick={()=>go('library')}>返回文献管理</Button></div></div>:<>
      {batchId && chapter && <BatchReviewBar batchId={batchId} chapter={chapter} dirty={dirty} processing={processing} go={go} setToast={setToast} onChanged={onChanged} sftReviewed={sftReviewed}/> }
      <div className="cw-chapter-bar"><label htmlFor="cw-chapter-select">当前章节</label><select id="cw-chapter-select" value={chapter?.id || ''} onChange={event=>changeChapter(event.target.value)}>{chapters.map((ch,i)=><option value={ch.id} key={ch.id}>{i+1}. {ch.title} · {ch.page_ids?.length || 0} 页{ch.status==='approved'?' · 已确认':''}</option>)}</select><button className="cw-text-button" onClick={()=>guard(()=>setDialog('chapters'))} disabled={processing}><Icon as={IconListDetails} size={15}/>调整章节</button><div className="cw-actions"><Button variant="purple" icon={IconArrowRight} onClick={()=>run('model')} disabled={!!busy || processing || !state.model.configured || !chapter}>{sourceCorpus?'重新整理这一章':'整理这一章'}</Button>{!state.model.configured && isAdmin && <button className="cw-text-button" onClick={()=>setDialog('model')}>配置AI服务</button>}</div></div>
      {processing && <div className="cw-job-strip" role="status"><Icon as={IconLoader2} className="cw-spinner"/><span><strong>{currentJob.mode==='demo'?'合成演示':'AI'}正在整理</strong><small>{currentJob.completed || 0} / {currentJob.chapter_count || 1} 章完成 · 自动更新</small></span><progress value={currentJob.completed || 0} max={currentJob.chapter_count || 1}/></div>}
      <div className="cw-compare-grid"><section className="cw-source-panel"><header className="cw-core-panel-heading"><h2>原始文献</h2><div className="cw-tabs"><button className={sourceTab==='pdf'?'active':''} onClick={()=>setSourceTab('pdf')}>PDF原件</button><button className={sourceTab==='ocr'?'active':''} onClick={()=>setSourceTab('ocr')}>OCR文字</button></div></header>
        <div className="cw-source-controls"><label><span>来源页</span><select value={page?.id || ''} onChange={event=>setPageId(event.target.value)} aria-label="定位当前章节的原始页">{members.map(p=><option value={p.id} key={p.id}>第 {p.number} 页{p.images?.length?` · ${p.images.length} 图`:''}</option>)}</select></label>{sourceTab==='pdf' && <><select aria-label="选择原始PDF" value={selectedPdf?.id || ''} onChange={event=>choosePdf(event.target.value)}><option value="" disabled>尚未添加PDF</option>{pdfs.map(pdf=><option value={pdf.id} key={pdf.id}>{pdf.name}</option>)}</select><button className="cw-text-button" onClick={()=>setDialog('pdfUpload')}><Icon as={IconUpload} size={14}/>{pdfs.length?'添加PDF':'补传PDF'}</button></>}</div>
        {sourceTab==='pdf' ? selectedPdf?.url?<><div className="cw-pdf-mapping"><span>OCR第 {page?.number || '—'} 页 → PDF第 {mappedPage} 页</span>{pdfPage!==mappedPage && <button onClick={()=>setPdfPage(mappedPage)}>回到来源页</button>}<button onClick={()=>setDialog('mapping')}>校正页码对应</button></div><div className="cw-pdf-host"><PdfViewer url={selectedPdf.url} pageNumber={pdfPage} onPageChange={setPdfPage}/></div></>:<div className="cw-missing-pdf"><Icon as={IconFileText} size={34}/><h3>添加原始PDF，逐页对照</h3><p>当前已保存OCR文字，但尚未关联PDF原件。可补传文献PDF，也可先查看OCR文字。</p><Button variant="primary" icon={IconUpload} onClick={()=>setDialog('pdfUpload')}>添加原始PDF</Button><button className="cw-text-button" onClick={()=>setSourceTab('ocr')}>先看OCR文字</button></div>:page?<div className="cw-ocr-view"><div className="cw-page-kicker">原始OCR结果 · 第 {page.number} 页</div><div className="cw-ocr-text"><PageMarkdown text={page.text} images={page.images || []} pdfs={pdfs} onPageLink={locatePage} onImage={openImage}/></div><div className="cw-image-heading"><strong>本页图片</strong><span>{page.images?.length || 0} 张</span></div>{page.images?.length?<div className="cw-image-grid">{page.images.map(image=><figure className="cw-image-card" key={image.id}><button onClick={()=>setDialog({image,page})} aria-label={`查看 ${image.caption || image.path}`}><img src={image.url} alt={image.caption || image.path}/></button><figcaption>{image.caption || image.path?.split('/').pop() || '原始图片'}</figcaption></figure>)}</div>:<p className="cw-quiet">本页没有关联图片。</p>}{page.unresolved_images?.length>0 && <div className="cw-alert warning">有 {page.unresolved_images.length} 个图片引用未匹配到文件，请核对原始路径。</div>}</div>:<div className="cw-empty">本章尚未关联原始页，请调整章节。</div>}
      </section><section className="cw-result-panel"><header className="cw-core-panel-heading"><h2>章节产物</h2>{chapter && <Badge status={chapter.status}>{chapter.status==='approved'?'已确认':chapter.status==='review'?'待确认':statusNames[chapter.status]}</Badge>}</header>
        {chapter?.error && <div className="cw-alert error"><Icon as={IconAlertTriangle}/>{chapter.error}</div>}
        {sourceCorpus?<><div className="cw-result-intro"><span>{result.human_edited?'已保存修改':'章节整理结果'}</span><small>第 {revision} 版{dirty?' · 有未保存修改':''}</small></div>{editor.conflict && <div className="cw-alert error">已有新的版本。你的草稿仍保留，请复制草稿或载入新版本后再保存。<button onClick={async()=>{const fresh=await refresh();acceptEditor(fresh?.chapters?.find(ch=>ch.id===chapter.id));}} disabled={!!busy}>放弃草稿并载入新版本</button></div>}
          <ChapterProducts editor={editor} setEditor={setEditor} result={result} document={document} members={members} pdfs={pdfs} productTab={productTab} setProductTab={setProductTab} markdownMode={markdownMode} setMarkdownMode={setMarkdownMode} onLocate={locatePage} onImage={openImage} onSchema={isAdmin?()=>setDialog('schema'):undefined} disabled={processing || !!busy}/>
          <div className="cw-editor-bottom"><span>{dirty?'图文、解读或字段有未保存修改':'同一版本保留两种产物'}</span><Button icon={IconCheck} onClick={saveRevision} disabled={!dirty || !!busy || processing || editor.conflict || !!validationIssues.length}>{busy==='save'?'正在保存…':'保存修改'}</Button></div>
          {validationIssues.length>0 && <div className="cw-alert warning">{validationIssues.slice(0,2).join(' ')}{validationIssues.length>2?` 另有 ${validationIssues.length-2} 项需补充。`:null}</div>}
          {dirty && sft.length>0 && <div className="cw-alert warning">产物已修改。保存后，原有 {sft.length} 组问答候选将移除；本次确认使用当前图文、解读和字段。</div>}
          {!dirty && chapter.status==='review' && sft.length>0 && <div className="cw-question-prompt"><Icon as={IconShieldCheck} size={16}/><span>本章还有 {sft.length} 组问答候选，一起使用前需要核对。</span><button onClick={()=>{setChecksOpen(true);setResultTab('sft');}}>核对问答</button></div>}
          {noPurpose && <div className="cw-alert warning">使用前需确认文献的授权范围。<button onClick={()=>setDialog('metadata')}>设置用途</button></div>}
          <div className="cw-primary-actions"><span className="cw-quiet">产出以批次抽检结论为准</span><Button icon={IconShieldCheck} onClick={()=>guard(()=>go(batchId?'batches?batch='+batchId:'batches?document='+document.id))}>{batchId?'返回处理与抽检':'加入文献批次'}</Button></div>
          {handoffReady && <div className="cw-success"><Icon as={IconCheck} size={17}/><span>已准备好，可用于{outputPurpose}。</span><div>{nextChapter && <button onClick={()=>changeChapter(nextChapter.id)}>下一章<Icon as={IconChevronRight} size={14}/></button>}<button onClick={()=>guard(()=>go?.('outputs'))}>查看产出<Icon as={IconArrowRight} size={14}/></button></div></div>}
        </>:<div className="cw-result-blank"><Icon as={IconDatabase} size={32}/><h3>{processing?'AI正在整理这一章…':'这一章还没有整理结果'}</h3><p>点击上方“整理这一章”。结果会保留原文来源，你可以直接修改或告诉AI重新组织。</p>{!state.model.configured && isAdmin && <Button icon={IconSettings} onClick={()=>setDialog('model')}>配置AI服务</Button>}</div>}
        <details className="cw-instructions"><summary>告诉AI怎么改<span>用一句话重新组织内容</span></summary><div><textarea value={instructions} maxLength={4000} onChange={event=>setInstructions(event.target.value)} rows={3} placeholder="例如：保留原始数据和单位，把这一章按“方法、结果、局限”整理；不要添加原文没有的结论。" aria-label="给AI的重新整理指令"/><div className="cw-model-line"><span>{state.model.configured?`${state.model.model} · ${state.model.endpoint}`:'尚未配置AI服务'}</span>{isAdmin && <button onClick={()=>setDialog('model')}>{state.model.configured?'更换服务':'配置服务'}</button>}</div><Button variant="purple" icon={IconArrowRight} onClick={()=>run('model',true)} disabled={!state.model.configured || !instructions.trim() || processing || !!busy || !chapter}>按指令重新整理</Button><small>会生成新的待确认版本。若有未保存修改，会先让你选择保存或放弃。</small></div></details>
      </section></div>
      {sourceCorpus && <details className="cw-checks-drawer" open={checksOpen} onToggle={event=>setChecksOpen(event.currentTarget.open)}><summary><Icon as={IconShieldCheck} size={17}/>核对图表、问答与来源<span>{sft.length} 组问答 · {(result.figures?.length || 0)+(result.tables?.length || 0)} 项图表</span></summary><div className="cw-tabs">{[['sft',`问答候选 (${sft.length})`],['figures','图表'],['provenance','来源与提醒']].map(([tab,label])=><button key={tab} className={resultTab===tab?'active':''} onClick={()=>setResultTab(tab)}>{label}</button>)}</div>{resultTab==='sft'?<div className="cw-sft-results">{sft.length?<>{sft.map((sample,index)=><article className="cw-sft-card" key={index}><header><Badge status="review">问答候选 {index+1}</Badge><small>{pageLabel(sample.source_pages)}</small></header><div><span>问题</span><p>{sample.question}</p></div><div><span>答案</span><p>{sample.answer}</p></div></article>)}{['review','approved'].includes(chapter.status) && !dirty && <label className="cw-checkbox cw-sft-check"><input type="checkbox" checked={sftReviewed} onChange={event=>setSftReviewed(event.target.checked)}/>我已对照原文核对这些问答和来源页</label>}</>:<p className="cw-quiet">本章没有问答候选。可单独使用已确认正文。</p>}</div>:resultTab==='figures'?<div className="cw-structured-results">{result.figures?.map((figure,index)=>{const image=images.find(img=>img.id===figure.image_id);return <article className="cw-figure" key={index}>{image && <button onClick={()=>setDialog({image,page:pages.find(p=>p.images?.some(img=>img.id===image.id))})}><img src={image.url} alt={figure.caption || image.path}/></button>}<div><h3>图 {index+1} · {figure.caption || '说明待确认'}</h3><p>{figure.interpretation || '未给出可核验解释，请对照原图。'}</p><small>{pageLabel(figure.source_pages)}</small></div></article>;})}{result.tables?.map((table,index)=><article className="cw-table-card" key={index}><h3>表 {index+1} · {table.title || '原文表格'}</h3><PageMarkdown text={table.markdown || '未提供表格正文。'}/><small>{pageLabel(table.source_pages)}</small></article>)}{!result.figures?.length && !result.tables?.length && <p className="cw-quiet">本章暂无结构化图表结果；左侧保留实际原件和图片。</p>}</div>:<div className="cw-provenance"><p><strong>本章来源：</strong>{pageLabel(members.map(p=>p.number))} · {document.title || document.name}</p>{result.facts?.map((fact,index)=><div className="cw-fact" key={index}><p>{fact.text}</p><small>{pageLabel(fact.source_pages)}</small></div>)}{result.warnings?.length?result.warnings.map((warning,index)=><div className="cw-alert warning" key={index}>{typeof warning==='string'?warning:warning.message || JSON.stringify(warning)}</div>):<p className="cw-quiet">未列出异常，不代表内容已经核验。请以原始PDF和实验记录为准。</p>}</div>}</details>}
      <details className="cw-more"><summary>更多设置与阶段文件<span>模型、用途、记录与下载</span></summary><div className="cw-more-actions">{isAdmin && <Button icon={IconSettings} onClick={()=>setDialog('model')}>AI服务设置</Button>}<Button icon={IconShieldCheck} onClick={()=>setDialog('metadata')}>文献用途</Button><Button icon={IconListDetails} onClick={()=>guard(()=>setDialog('chapters'))}>章节编排</Button><Button icon={IconUpload} onClick={()=>setDialog('pdfUpload')}>添加原始PDF</Button>{isAdmin && <Button icon={IconTable} onClick={()=>setDialog('schema')}>字段模板</Button>}</div><div className="cw-artifact-links"><a href={`/api/chapters/download/${encodeURIComponent(document.id)}?stage=pages`} download><Icon as={IconDownload} size={15}/>下载逐页资料</a><a href={`/api/chapters/download/${encodeURIComponent(document.id)}?stage=chapters`} download><Icon as={IconDownload} size={15}/>下载章节编排</a><a className={!reviewedCount?'disabled':''} href={reviewedCount?`/api/chapters/download/${encodeURIComponent(document.id)}?stage=corpus`:undefined} download><Icon as={IconDownload} size={15}/>下载已确认语料</a><span>{reviewedCount} 章已确认 · {materializedCount} 份已可用于产出</span></div><div className="cw-job-history"><h3>AI整理记录</h3>{currentJobs.length?<div className="cw-job-table-wrap"><table className="cw-job-table"><thead><tr><th>方式</th><th>状态</th><th>完成章节</th><th>时间</th><th>结果</th></tr></thead><tbody>{currentJobs.map(job=><tr key={job.id}><td>{job.mode==='model'?'AI整理':'规则整理'}</td><td>{job.status==='completed'?'已完成':statusNames[job.status]}</td><td>{job.completed || 0}/{job.chapter_count || 0}</td><td>{dateLabel(job.created_at)}</td><td>{job.error || (job.status==='completed'?'已保存':'处理中')}</td></tr>)}</tbody></table></div>:<p className="cw-quiet">尚无整理记录。</p>}</div>{chapter && <RevisionHistory chapterId={chapter.id} revision={revision} status={chapter.status} onLocate={locatePage}/>}</details>
    </>}
    {dialog==='import' && <ChapterImportDialog context={libraryContext} onClose={()=>setDialog(null)} onImported={async outcome=>{chapterRef.current='';await refresh(outcome.document?.id || outcome.documents?.[0]?.id);setSourceTab('pdf');setDialog(null);notify('文献已导入，可以开始对照整理。');}}/>}
    {dialog==='schema' && isAdmin && document && <FieldSchemaDialog document={document} onClose={()=>setDialog(null)} onSaved={async()=>{await refresh();setDialog(null);notify('字段模板已保存。下次模型整理使用新模板，已有结果保留原模板快照。');}}/>}
    {dialog==='model' && isAdmin && <ModelDialog model={state.model} onClose={()=>setDialog(null)} onSaved={async()=>{await refresh();setDialog(null);notify('AI服务设置已保存。');}}/>}
    {dialog==='metadata' && document && <MetadataDialog document={document} onClose={()=>setDialog(null)} onSaved={async()=>{await refresh();await onChanged?.();setDialog(null);notify('文献用途已保存。');}}/>}
    {dialog==='chapters' && detail && <ChapterEditor detail={detail} onClose={()=>setDialog(null)} onSaved={async()=>{const fresh=await refresh();acceptEditor(fresh?.chapters?.find(ch=>ch.id===chapterRef.current));await onChanged?.();setDialog(null);notify('章节已调整，请重新整理受影响章节。');}}/>}
    {dialog==='pdfUpload' && document && <PdfUploadDialog document={document} onClose={()=>setDialog(null)} onSaved={async()=>{await refresh();setSourceTab('pdf');setDialog(null);notify('原始PDF已保存，可逐页对照。');}}/>}
    {dialog==='mapping' && document && <PageMappingDialog document={document} sourcePage={page?.number || 1} onClose={()=>setDialog(null)} onSaved={async()=>{await refresh();setDialog(null);notify('页码对应已保存，不会修改原始OCR内容。');}}/>}
    {dialog==='unsaved' && <Dialog title="还有未保存的修改" subtitle="保存后会生成新的待确认版本；你也可以放弃草稿继续。" onClose={()=>{guardAction.current=null;setDialog(null);}}><p className="cw-guard-message">{sft.length?'保存产物修改会移除当前版本的问答候选。':'原始文献和已保存版本都会保留。'}</p>{error && <div className="cw-alert error">{error}</div>}<div className="cw-modal-footer"><Button onClick={()=>{guardAction.current=null;setDialog(null);}} disabled={!!busy}>继续编辑</Button><Button onClick={()=>leaveDraft(false)} disabled={!!busy}>放弃修改并继续</Button><Button variant="primary" onClick={()=>leaveDraft(true)} disabled={!!busy || editor.conflict || !!validationIssues.length}>保存并继续</Button></div></Dialog>}
    {typeof dialog==='object' && dialog?.image && <Dialog title={dialog.image.caption || '原始页面图片'} subtitle={`第 ${dialog.page?.number || '—'} 页 · ${dialog.image.path || ''}`} wide onClose={()=>setDialog(null)}><div className="cw-image-preview"><img src={dialog.image.url} alt={dialog.image.caption || dialog.image.path}/></div><div className="cw-modal-footer"><a className="cw-button primary" href={dialog.image.url} target="_blank" rel="noreferrer">打开原始图片</a><Button onClick={()=>setDialog(null)}>关闭</Button></div></Dialog>}
  </div>;
}

function PdfUploadDialog({document,onClose,onSaved}) {
  const [file,setFile]=useState(null), [busy,setBusy]=useState(false), [error,setError]=useState('');
  async function submit(event) {event.preventDefault();if(!file) return;setBusy(true);setError('');try{if(file.size>16*1048576) throw Error('原始PDF限16 MiB，请压缩后再上传。');await request('/documents/'+encodeURIComponent(document.id)+'/pdfs',{name:file.name,content_base64:await encode(file),select:true});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  return <Dialog title="添加原始PDF" subtitle="保留实际PDF原件，方便逐页核对。可为同一文献添加多个PDF。" onClose={onClose}><form onSubmit={submit}><label className="cw-upload-box"><Icon as={IconUpload} size={30}/><strong>{file?file.name:'选择文献原始PDF'}</strong><span>{file?byteSize(file.size):'单文件不超过16 MiB'}</span><input type="file" accept=".pdf,application/pdf" onChange={event=>{setFile(event.target.files[0] || null);setError('');}} aria-label="选择原始PDF"/></label>{error && <div className="cw-alert error">{error}</div>}<div className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="primary" disabled={busy || !file}>{busy?'正在保存…':'添加并打开PDF'}</Button></div></form></Dialog>;
}
function PageMappingDialog({document,sourcePage,onClose,onSaved}) {
  const [offset,setOffset]=useState(String(document.pdf_page_offset || 0)), [busy,setBusy]=useState(false), [error,setError]=useState('');
  async function submit(event) {event.preventDefault();const value=Number(offset);if(!Number.isInteger(value)){setError('偏移量必须是整数。');return;}setBusy(true);setError('');try{await request('/documents/'+encodeURIComponent(document.id),{pdf_page_offset:value});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  return <Dialog title="校正PDF页码对应" subtitle="有封面或前言时，可手动指定PDF与OCR的页码偏移。系统不会推断对应关系。" onClose={onClose}><form onSubmit={submit}><label className="cw-field"><span>PDF页码偏移量</span><input type="number" step="1" value={offset} onChange={event=>setOffset(event.target.value)} required/><small>PDF页码 = OCR页码 + 偏移量。例如OCR第1页位于PDF第3页，填写2。</small></label><div className="cw-alert">当前来源：OCR第 {sourcePage} 页 → PDF第 {Number(sourcePage)+Number(offset || 0)} 页</div>{error && <div className="cw-alert error">{error}</div>}<div className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="primary" disabled={busy}>保存对应关系</Button></div></form></Dialog>;
}
function RevisionHistory({chapterId,revision,status,onLocate}) {
  const [history,setHistory]=useState([]), [error,setError]=useState('');
  useEffect(()=>{let active=true;setHistory([]);request('/history/'+encodeURIComponent(chapterId)).then(value=>{if(active){setHistory(value.history || []);setError('');}}).catch(failure=>{if(active)setError(failure.message);});return()=>{active=false;};},[chapterId,revision,status]);
  const names={prepared_example:'载入真实论文预置整理',codex_prepared_example:'载入真实论文预置整理',run_requested:'提交AI整理',generated:'AI整理完成',manual_revised:'保存人工修改',approved:'确认使用',rejected:'暂不采用',manual_revision:'保存人工修改',manual_edit:'保存人工修改',revise:'保存人工修改',model:'AI重新整理',model_completed:'AI整理完成',demo:'演示整理',approve:'确认使用',reject:'暂不采用',review:'人工确认'};
  return <div className="cw-revision-history"><h3>本章修改记录</h3><p className="cw-history-hint">展开记录可回看当时的正文和修改要求，历史快照保留原样。</p>{error?<p className="cw-quiet">{error}</p>:history.length?<ul>{history.map(item=>{
    const payload=item.payload || {}, corpus=payload.corpus || payload.result?.corpus, previous=payload.previous_result?.corpus, instructions=payload.instructions || payload.result?.instructions;
    const samples=payload.sft_samples || payload.result?.sft_samples || [];
    return <li className="cw-history-item" key={item.id}><details className="cw-history-entry"><summary><span>第 {item.revision} 版</span><strong>{names[item.event] || item.event}</strong><small>{dateLabel(item.created_at)}</small><Icon as={IconChevronRight} size={14}/></summary><div className="cw-history-payload">
      {instructions && <section><h4>告诉AI的修改要求</h4><p className="cw-history-instruction">{instructions}</p></section>}
      {item.event==='run_requested' && !instructions && <p className="cw-history-hint">按原始OCR内容重新整理，没有附加修改要求。</p>}
      {corpus && <section><h4>本次正文快照</h4><pre>{corpus}</pre></section>}
      {previous && <section><h4>{item.event==='run_requested'?'提交时的上版正文':'修改前正文'}{payload.previous_revision != null?` · 第 ${payload.previous_revision} 版`:''}</h4><pre>{previous}</pre></section>}
      <HistoryProductSnapshot result={payload.result || (item.event==='run_requested'?null:payload)} label="本次产物" onLocate={onLocate}/>
      <HistoryProductSnapshot result={payload.previous_result} label="上版产物" onLocate={onLocate}/>
      {samples.length>0 && <section><h4>当时的问答候选 · {samples.length} 组</h4>{samples.map((sample,index)=><div className="cw-history-question" key={index}><p><strong>问题：</strong>{sample.question}</p><p><strong>答案：</strong>{sample.answer}</p><small>来源页：{sample.source_pages?.join('、') || '未标注'}</small></div>)}</section>}
      {!corpus && !previous && !instructions && item.event!=='run_requested' && <p className="cw-history-hint">该记录没有正文快照。</p>}
    </div></details></li>;
  })}</ul>:<p className="cw-quiet">保存修改或重新整理后，会在这里保留版本记录。</p>}</div>;
}
function HistoryProductSnapshot({result,label,onLocate}) {
  if(!result) return null;
  const interpretation=interpretationOf(result), fields=result.structured_fields || [], labels=new Map((result.extraction_schema || []).map(field=>[field.key,field.label]));
  return <>{interpretation.text && <section><h4>{label} · 独立解读</h4><pre>{interpretation.text}</pre><SourcePages values={interpretation.source_pages} onLocate={onLocate}/></section>}{fields.length>0 && <section><h4>{label} · 字段快照{result.schema_revision!=null?`（模板第 ${result.schema_revision} 版）`:''}</h4><div className="cw-history-field-snapshots">{fields.map(field=><article key={field.key}><header><strong>{labels.get(field.key) || field.key}</strong><Badge status={field.status==='found'?'approved':'pending'}>{field.status==='found'?'找到':'未提取'}</Badge></header><p><b>值：</b>{field.value==null?'null':typeof field.value==='boolean'?(field.value?'是':'否'):String(field.value)}</p>{field.evidence && <p><b>原文证据：</b>{field.evidence}</p>}<SourcePages values={field.source_pages} onLocate={onLocate}/>{field.image_ids?.length>0 && <small>来源图像：{field.image_ids.join('、')}</small>}</article>)}</div></section>}</>;
}

export function ChapterImportDialog({context,onClose,onImported}) {
  const [mode,setMode] = useState('folder'), [files,setFiles] = useState([]), [name,setName] = useState(''), [purpose,setPurpose] = useState('both'), [license,setLicense] = useState('unknown'), [error,setError] = useState(''), [busy,setBusy] = useState(false);
  const total = files.reduce((sum,file)=>sum+file.size,0);
  function choose(list) {const selected = Array.from(list || []);setError('');if(selected.length>2000) {setError('每篇文献最多 2000 个文件。');return;}if(selected.some(file=>file.size>16*1048576)) {setError('单个文件限 16 MiB，请拆分大文件。');return;}if(selected.reduce((sum,file)=>sum+file.size,0)>32*1048576) {setError('本次导入总量限 32 MiB，请按文献拆分。');return;}setFiles(selected);if(selected.length) setName(mode==='folder'?selected[0].webkitRelativePath.split('/')[0] || '导入文献':selected[0].name.replace(/\.zip$/i,''));}
  async function submit(event) {event.preventDefault();setBusy(true);setError('');try {const body = {name:name.trim() || '导入文献',purpose,license,record_id:context?.record_id,source_id:context?.source_id};if(mode==='folder') body.files = await Promise.all(files.map(async file => ({path:file.webkitRelativePath || file.name,content_base64:await encode(file)})));else body.content_base64 = await encode(files[0]);const outcome = await request('/import',body);await onImported(outcome);} catch(failure) {setError(failure.message);} finally {setBusy(false);}}
  return <Dialog title="导入文献" subtitle={context?.record_name?`保存到 ${context.source_name} / ${context.record_name}`:'将文献归入当前物种 / 数据条目。保留原始 PDF、逐页文字与图片。'} onClose={onClose}><form onSubmit={submit}><div className="cw-modal-body"><div className="cw-tabs">{[['folder','文献文件夹'],['zip','OCR 结果 ZIP']].map(([key,title]) => <button type="button" key={key} className={mode===key?'active':''} onClick={() => {setMode(key);setFiles([]);setName('');setError('');}}>{title}</button>)}</div><label className="cw-upload-box"><Icon as={mode==='folder'?IconFolder:IconUpload} size={32}/><strong>{mode==='folder'?'选择一篇文献的资料文件夹':'选择逐页 OCR 结果 ZIP'}</strong><span>{mode==='folder'?'逐页 .md / .json ＋ images 图片 ＋ 原始 PDF（可选）':'保留目录结构，图片路径与页文档保持一致'}</span><small>单文件 ≤ 16 MiB · 总量 ≤ 32 MiB · 最多 2000 文件</small>{mode==='folder'?<input key="folder" aria-label="选择文献文件夹" type="file" webkitdirectory="" directory="" multiple onChange={event=>choose(event.target.files)}/>:<input key="zip" aria-label="选择OCR资料ZIP" type="file" accept=".zip" onChange={event=>choose(event.target.files)}/>}</label>{files.length>0 && <div className="cw-file-summary"><span><Icon as={IconCheck}/>{files.length} 个文件已选择</span><strong>{byteSize(total)}</strong><div>{files.slice(0,4).map((file,i)=><p key={i}>{file.webkitRelativePath || file.name}</p>)}{files.length>4 && <small>另有 {files.length-4} 个文件</small>}</div></div>}<label className="cw-field"><span>文献名称</span><input value={name} onChange={event=>setName(event.target.value)} maxLength={150} required placeholder="例如：水稻光合作用研究资料"/></label><div className="cw-field-grid"><label className="cw-field"><span>计划用途</span><select value={purpose} onChange={event=>setPurpose(event.target.value)}><option value="both">训练数据 + 知识库</option><option value="training">仅训练数据</option><option value="kb">仅知识库</option></select></label><label className="cw-field"><span>授权范围</span><select value={license} onChange={event=>setLicense(event.target.value)}><option value="unknown">用途待核对</option><option value="self_owned">自有 / 已获授权</option><option value="research_only">仅内部知识库</option></select></label></div><div className="cw-alert"><Icon as={IconFileText}/><span>这里接收已有 OCR 结果，不重新识别 PDF。页标题、原文和实际图片保留为输入；模型加工与人工修订另行保存。</span></div>{error && <div className="cw-alert error" role="alert"><Icon as={IconAlertTriangle}/>{error}</div>}</div><footer className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="primary" icon={busy?IconLoader2:IconFolder} disabled={busy || !files.length || !name.trim()}>{busy?'正在导入并建立逐页记录…':'导入并预览'}</Button></footer></form></Dialog>;
}

function ModelDialog({model,onClose,onSaved}) {
  const [endpoint,setEndpoint] = useState(model.endpoint || ''), [name,setName] = useState(model.model || ''), [key,setKey] = useState(''), [vision,setVision] = useState(!!model.vision), [clearKey,setClearKey] = useState(false), [busy,setBusy] = useState(false), [error,setError] = useState('');
  async function submit(event) {event.preventDefault();setBusy(true);setError('');try {await request('/model',{endpoint:endpoint.trim(),model:name.trim(),vision,...(clearKey?{key:null}:key.trim()?{key:key.trim()}:{})});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  return <Dialog title="模型服务设置" subtitle="真实加工会调用你指定的服务，并把原始章节内容发送给该端点。" onClose={onClose}><form onSubmit={submit}><div className="cw-modal-body"><div className="cw-model-notice"><Icon as={IconSettings}/><div><strong>OpenAI 兼容接口</strong><p>填写 API 基础地址和模型 ID。由你选择模型与服务，本系统不会默认调用其他供应商。</p></div></div><label className="cw-field"><span>API 基础地址</span><input type="url" required value={endpoint} onChange={event=>setEndpoint(event.target.value)} placeholder="https://你的服务地址/v1" autoComplete="off"/><small>填写基础地址；服务需支持 chat completions 接口。</small></label><label className="cw-field"><span>模型 ID</span><input value={name} required maxLength={200} onChange={event=>setName(event.target.value)} placeholder="填写服务提供的准确模型名称"/></label><label className="cw-field"><span>API 密钥 {model.has_key && <Badge status="approved">本机已保存</Badge>}</span><input type="password" value={key} onChange={event=>{setKey(event.target.value);setClearKey(false);}} placeholder={model.has_key?'留空保留已保存密钥':'输入服务所需的密钥'} autoComplete="new-password" disabled={clearKey}/><small>密钥不会回显到页面。仅配置地址与模型，不代表服务已经验证可用。</small></label>{model.has_key && <label className="cw-checkbox"><input type="checkbox" checked={clearKey} onChange={event=>setClearKey(event.target.checked)}/>保存时清除本机现有密钥</label>}<label className="cw-checkbox cw-vision"><input type="checkbox" checked={vision} onChange={event=>setVision(event.target.checked)}/><span><strong>包含章节图片</strong><small>仅当所选模型支持图像输入时开启；文本模型请关闭。</small></span></label><div className="cw-alert warning"><Icon as={IconLock}/><span>请核对文献授权与模型 API 使用条件。真实运行可能产生费用，并将所选章节文本{vision?'及图片':''}发送到上述端点。</span></div>{error && <div className="cw-alert error" role="alert">{error}</div>}</div><footer className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button variant="primary" type="submit" icon={IconCheck} disabled={busy || !endpoint.trim() || !name.trim()}>{busy?'正在保存…':'保存模型设置'}</Button></footer></form></Dialog>;
}

function MetadataDialog({document,onClose,onSaved}) {
  const [title,setTitle] = useState(document.title || document.name || ''), [purpose,setPurpose] = useState(document.purpose || 'both'), [license,setLicense] = useState(document.license || 'unknown'), [busy,setBusy] = useState(false), [error,setError] = useState('');
  async function submit(event) {event.preventDefault();setBusy(true);setError('');try {await request('/documents/'+encodeURIComponent(document.id),{title:title.trim(),purpose,license});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  return <Dialog title="文献用途设置" subtitle="保留用途确认记录，并同步已派生的章节语料资产。" onClose={onClose}><form onSubmit={submit}><div className="cw-modal-body"><label className="cw-field"><span>文献名称</span><input required maxLength={150} value={title} onChange={event=>setTitle(event.target.value)}/></label><div className="cw-field-grid"><label className="cw-field"><span>计划用途</span><select value={purpose} onChange={event=>setPurpose(event.target.value)}>{Object.entries(purposeNames).map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label><label className="cw-field"><span>授权范围</span><select value={license} onChange={event=>setLicense(event.target.value)}>{Object.entries(licenseNames).map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label></div><div className="cw-alert"><Icon as={IconShieldCheck}/><span>用途待核对时保留原文和加工结果，待确认授权后再用于产出。选择标签需要基于你的授权依据，系统不会自动判断版权或 API 条款。</span></div>{error && <div className="cw-alert error" role="alert">{error}</div>}</div><footer className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="primary" icon={IconCheck} disabled={busy || !title.trim()}>{busy?'正在保存…':'保存用途设置'}</Button></footer></form></Dialog>;
}

function ChapterEditor({detail,onClose,onSaved}) {
  const [drafts,setDrafts] = useState((detail.chapters || []).map(ch=>({title:ch.title,page_ids:[...(ch.page_ids || [])]}))), [active,setActive] = useState(0), [busy,setBusy] = useState(false), [error,setError] = useState('');
  const pages = detail.pages || [], assigned = new Set(drafts.flatMap(d=>d.page_ids)), missing = pages.filter(p=>!assigned.has(p.id)).length;
  function title(value) {setDrafts(old=>old.map((ch,i)=>i===active?{...ch,title:value}:ch));}
  function assign(pageId,checked) {setDrafts(old=>old.map((ch,i)=>({...ch,page_ids:i===active && checked?[...new Set([...ch.page_ids,pageId])]:ch.page_ids.filter(id=>id!==pageId)})));}
  function add() {setDrafts(old=>[...old,{title:`新章节 ${old.length+1}`,page_ids:[]}]);setActive(drafts.length);}
  function remove(index) {if(drafts.length<=1) return;const removed = drafts[index];setDrafts(old=>{const next=old.filter((_,i)=>i!==index);next[0]={...next[0],page_ids:[...new Set([...next[0].page_ids,...removed.page_ids])]};return next;});setActive(0);}
  async function save() {setBusy(true);setError('');try{await request('/rebuild',{document_id:detail.document.id,chapters:drafts.map(d=>({title:d.title.trim(),page_ids:d.page_ids}))});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  const selected = drafts[active];
  return <Dialog title="章节编排" subtitle="编辑章节标题并指定原始页归属；同一页只归属一个章节。" onClose={onClose} wide><div className="cw-modal-body"><div className="cw-editor-layout"><div className="cw-draft-list"><div className="cw-rail-heading"><strong>{drafts.length} 个章节</strong><button className="cw-text-button" onClick={add}><Icon as={IconPlus} size={15}/>新增</button></div>{drafts.map((ch,i)=><div className={`cw-draft-item ${i===active?'active':''}`} key={i}><button onClick={()=>setActive(i)}><span>{String(i+1).padStart(2,'0')}</span><strong>{ch.title || '未命名章节'}</strong><small>{ch.page_ids.length} 页</small></button><button className="cw-icon-button" aria-label={`删除章节 ${ch.title}`} disabled={drafts.length<=1} onClick={()=>remove(i)}><Icon as={IconTrash} size={15}/></button></div>)}</div><div className="cw-page-assignment-panel">{selected && <><label className="cw-field"><span>章节标题</span><input value={selected.title} maxLength={200} onChange={event=>title(event.target.value)} aria-label="当前章节标题"/></label><div className="cw-assignment-heading"><strong>原始页归属</strong><span>{selected.page_ids.length} 页已选择</span></div><div className="cw-page-assignments">{pages.map(page=>{const owner=drafts.findIndex(d=>d.page_ids.includes(page.id));return <label className="cw-page-option" key={page.id}><input type="checkbox" checked={selected.page_ids.includes(page.id)} onChange={event=>assign(page.id,event.target.checked)}/><span><strong>第 {page.number} 页</strong><small>{String(page.text || '无文字内容').replace(/[#\n]/g,' ').slice(0,76)}</small></span><span className="cw-page-option-meta">{page.images?.length || 0} 图<small>{owner>=0?`章节 ${owner+1}`:'未分配'}</small></span></label>;})}</div></>}</div></div><div className="cw-alert warning"><Icon as={IconAlertTriangle}/><span>{missing?`还有 ${missing} 页未分配。`:'全部原始页已归属章节。'} 修改编排后，受影响的旧语料需要重新加工与复核。删除章节时，其页面移入第一章。</span></div>{error && <div className="cw-alert error" role="alert">{error}</div>}</div><footer className="cw-modal-footer"><Button onClick={onClose} disabled={busy}>取消</Button><Button variant="primary" icon={IconCheck} onClick={save} disabled={busy || missing>0 || !drafts.length || drafts.some(d=>!d.title.trim() || !d.page_ids.length)}>{busy?'正在保存…':'保存章节编排'}</Button></footer></Dialog>;
}

const plantFieldTemplate=[
  {key:'scientific_name',label:'植物学名',type:'text',description:'文中明确使用的拉丁学名，保留原文拼写。'},
  {key:'family',label:'科',type:'text',description:'文中明确提及的分类科名。'},
  {key:'growth_habit',label:'生长型',type:'text',description:'文献明确描述的草本、灌木、藤本等生长型。'},
  {key:'leaf_shape',label:'叶形',type:'text',description:'原文叶片形态描述，不根据照片推断。'},
  {key:'plant_height_cm',label:'株高（cm）',type:'number',description:'仅填写文中明确报告、能确定单位为cm的单个数值；范围保留在证据，不能取均值。'},
  {key:'habitat',label:'生境',type:'text',description:'文中记载的生境或分布环境。'},
  {key:'flowering_period',label:'花期',type:'text',description:'文中明确记载的开花时间。'},
  {key:'is_endemic',label:'是否特有',type:'boolean',description:'仅当文中明确说明是或不是特有种时填写，未提及为null。'}
];
const fieldTypeNames={text:'文本',number:'数值',boolean:'是 / 否'};
function SourcePages({values=[],onLocate}) {
  return values.length?<span className="cw-page-links">{values.map(number=><button type="button" key={number} onClick={()=>onLocate(Number(number))}>第 {number} 页<Icon as={IconExternalLink} size={11}/></button>)}</span>:<span className="cw-reference-empty">未标注来源页</span>;
}
function PageSelector({pages,values=[],onChange,disabled=false}) {
  return <details className="cw-page-picker"><summary>选择来源页{values.length?` (${values.length})`:''}</summary><div>{pages.map(page=><label key={page.id}><input type="checkbox" disabled={disabled} checked={values.includes(page.number)} onChange={event=>onChange(event.target.checked?[...values,page.number].sort((a,b)=>a-b):values.filter(value=>value!==page.number))}/>第 {page.number} 页</label>)}</div></details>;
}
function FieldResults({schema,fields,members,onChange,onLocate,onImage,disabled}) {
  const [expanded,setExpanded]=useState(null);
  const images=members.flatMap(page=>page.images || []);
  function update(key,patch) {
    const previous=fields.find(field=>field.key===key) || {key,value:null,status:'not_found',evidence:'',source_pages:[],image_ids:[]};
    let next={...previous,...patch};
    if(patch.status==='not_found') next={key,value:null,status:'not_found',evidence:'',source_pages:[],image_ids:[]};
    if(patch.source_pages) {const allowed=new Set(members.filter(page=>patch.source_pages.includes(page.number)).flatMap(page=>page.images?.map(image=>image.id) || []));next.image_ids=(next.image_ids || []).filter(id=>allowed.has(id));}
    onChange([...fields.filter(field=>field.key!==key),next]);
  }
  return <div className="cw-field-results">{schema.map((definition,index)=>{
    const record=fields.find(field=>field.key===definition.key), field=record || {key:definition.key,value:null,status:'not_found',evidence:'',source_pages:[],image_ids:[]}, found=field.status==='found', open=expanded===definition.key || expanded===null && index===0;
    const available=members.filter(page=>field.source_pages?.includes(page.number)).flatMap(page=>page.images || []);
    const value=typeof field.value==='boolean'?(field.value?'是':'否'):field.value==null?'—':String(field.value);
    return <article className={`cw-field-result ${open?'open':''}`} key={definition.key}><button className="cw-field-summary" type="button" onClick={()=>setExpanded(open?'':definition.key)}><span><strong>{definition.label}</strong><small>{definition.key} · {fieldTypeNames[definition.type]}</small></span><span className="cw-field-summary-value">{found?value:'—'}</span><Badge status={record?found?'approved':'pending':'pending'}>{record?found?'找到':'未提取':'尚未抽取'}</Badge><Icon as={IconChevronRight} size={14}/></button>{open && <div className="cw-field-detail"><p className="cw-field-description">{definition.description || '仅填写有原文证据的内容。'}</p><div className="cw-field-value-row"><label><span>记录状态</span><select value={field.status} disabled={disabled} onChange={event=>update(definition.key,{status:event.target.value})}><option value="not_found">未提取 · 留空</option><option value="found">找到 · 有原文证据</option></select></label><label><span>{definition.label}</span>{definition.type==='boolean'?<select aria-label={definition.label} value={field.value==null?'':String(field.value)} disabled={disabled || !found} onChange={event=>update(definition.key,{value:event.target.value===''?null:event.target.value==='true'})}><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>:<input type={definition.type==='number'?'number':'text'} step={definition.type==='number'?'any':undefined} aria-label={definition.label} disabled={disabled || !found} value={field.value==null?'':String(field.value)} placeholder={found?'填写原文支持的值':'未找到可靠依据，不补全'} onChange={event=>update(definition.key,{value:definition.type==='number'?(event.target.value===''?null:Number(event.target.value)):event.target.value})}/>}</label></div>
      {found?<><label className="cw-evidence-field"><span>原文证据 · 原句摘录</span><textarea rows={3} value={field.evidence || ''} disabled={disabled} onChange={event=>update(definition.key,{evidence:event.target.value})} placeholder="从来源页复制支持该字段的原文，不能写推测。"/></label><div className="cw-reference-row"><SourcePages values={field.source_pages} onLocate={onLocate}/><PageSelector pages={members} values={field.source_pages} onChange={source_pages=>update(definition.key,{source_pages})} disabled={disabled}/></div>{available.length>0 && <details className="cw-field-image-picker"><summary>证据图像 ({field.image_ids?.length || 0})</summary><div>{available.map(image=><label key={image.id}><input type="checkbox" disabled={disabled} checked={field.image_ids?.includes(image.id) || false} onChange={event=>update(definition.key,{image_ids:event.target.checked?[...(field.image_ids || []),image.id]:(field.image_ids || []).filter(id=>id!==image.id)})}/><span>{image.caption || image.path?.split('/').pop()}</span><button type="button" onClick={()=>onImage(image)}>看原图</button></label>)}</div></details>}{field.image_ids?.length>0 && <div className="cw-field-image-links">{field.image_ids.map(id=>{const image=images.find(item=>item.id===id);return image?<button type="button" onClick={()=>onImage(image)} key={id}><img src={image.url} alt={image.caption || '来源图片'} loading="lazy"/><span>{image.caption || image.path?.split('/').pop()}</span></button>:null;})}</div>}</>:<p className="cw-field-null-note">该字段保留 null；不从其他论文、常识或图像猜测补全。</p>}
    </div>}</article>;
  })}</div>;
}
function ChapterProducts({editor,setEditor,result,document,members,pdfs,productTab,setProductTab,markdownMode,setMarkdownMode,onLocate,onImage,onSchema,disabled}) {
  const images=members.flatMap(page=>page.images || []), schema=result.extraction_schema || [], currentSchema=document.extraction_schema || [], fields=editor.fields || [], interpretation=editor.interpretation || {text:'',source_pages:[]};
  const found=fields.filter(field=>field.status==='found').length, schemaChanged=(result.schema_revision || 0)!==(document.schema_revision || 0);
  function patch(values) {setEditor(previous=>({...previous,...values}));}
  return <div className="cw-products"><div className="cw-product-tabs" role="tablist" aria-label="章节产物类型">{[['markdown','图文 Markdown',IconFileText],['fields','字段提取',IconTable]].map(([key,label,Component])=><button key={key} role="tab" aria-selected={productTab===key} className={productTab===key?'active':''} onClick={()=>setProductTab(key)}><Icon as={Component} size={16}/>{label}{key==='fields' && schema.length>0 && <small>{schema.length}</small>}</button>)}</div>
    {productTab==='markdown'?<><div className="cw-markdown-toolbar"><span>正文与原始图像链接</span><div className="cw-segment"><button className={markdownMode==='preview'?'active':''} onClick={()=>setMarkdownMode('preview')}>预览</button><button className={markdownMode==='edit'?'active':''} onClick={()=>setMarkdownMode('edit')}>编辑</button></div></div>{markdownMode==='preview'?<div className="cw-markdown-preview"><PageMarkdown text={editor.text} images={images} pdfs={pdfs} onPageLink={onLocate} onImage={onImage}/></div>:<><textarea className="cw-main-editor cw-markdown-editor" value={editor.text} onChange={event=>patch({text:event.target.value})} readOnly={disabled} aria-label="编辑本章图文Markdown" spellCheck={false}/>{images.length>0 && <details className="cw-insert-images"><summary>插入本章原始图片 ({images.length})</summary><div>{images.map(image=><button type="button" key={image.id} disabled={disabled} onClick={()=>patch({text:editor.text+`\n\n![${(image.caption || '原始图片').replace(/[\[\]\n]/g,' ')}](${image.url})\n`})}><Icon as={IconPhoto} size={14}/>{image.caption || image.path?.split('/').pop()}</button>)}</div></details>}</>}
      <section className="cw-interpretation"><header><h3>独立章节解读</h3><span>与原文正文分开保存</span></header><p>用于解释本章内容、意义与局限；不是原文摘要，也不替代原文事实。</p><textarea value={interpretation.text} rows={3} readOnly={disabled} aria-label="编辑独立章节解读" placeholder="目前没有独立解读。填写时说明依据和局限，并选择出处页。" onChange={event=>patch({interpretation:{...interpretation,text:event.target.value}})}/><div className="cw-reference-row"><SourcePages values={interpretation.source_pages} onLocate={onLocate}/><PageSelector pages={members} values={interpretation.source_pages} disabled={disabled} onChange={source_pages=>patch({interpretation:{...interpretation,source_pages}})}/></div></section>
    </>:<div className="cw-extraction"><div className="cw-extraction-heading"><span>{schema.length?`${schema.length} 个字段 · ${found} 找到 · ${fields.filter(field=>field.status==='not_found').length} 未提取`:'先设置要从文献提取的字段'}</span>{onSchema && <button type="button" onClick={onSchema}><Icon as={IconSettings} size={14}/>编辑字段模板</button>}</div>{schemaChanged && <div className="cw-alert warning">当前模板为第 {document.schema_revision || 0} 版，以下结果使用第 {result.schema_revision || 0} 版。重新整理后才会应用新模板。</div>}{schema.length?<><p className="cw-extraction-note">只填原文有可靠依据的字段；未提及或有歧义时留空。展开字段可修改值、核对原句和定位来源。</p><FieldResults schema={schema} fields={fields} members={members} onChange={fields=>patch({fields})} onLocate={onLocate} onImage={onImage} disabled={disabled}/></>:<div className="cw-fields-empty"><Icon as={IconTable} size={30}/><strong>{currentSchema.length?'已设置模板，当前版本尚未提取字段':'定义你需要的植物性状'}</strong><p>{currentSchema.length?`当前模板包含 ${currentSchema.length} 个字段。配置真实模型后重新整理，生成有原文证据的字段结果。`:'添加学名、叶形、生境等字段，并说明字段含义。模板决定后续模型抽取内容。'}</p>{onSchema && <Button icon={IconSettings} onClick={onSchema}>{currentSchema.length?'查看字段模板':'设置字段模板'}</Button>}</div>}</div>}
  </div>;
}
function FieldSchemaDialog({document,onClose,onSaved}) {
  const [schema,setSchema]=useState(JSON.parse(JSON.stringify(document.extraction_schema || []))), [busy,setBusy]=useState(false), [error,setError]=useState('');
  function update(index,patch) {setSchema(previous=>previous.map((field,i)=>i===index?{...field,...patch}:field));}
  async function submit(event) {event.preventDefault();setError('');const keys=new Set();for(const field of schema){if(!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(field.key)){setError('字段 key 以字母开头，仅使用字母、数字、下划线，最长64字符。');return;}if(keys.has(field.key)){setError(`字段 key 重复：${field.key}`);return;}keys.add(field.key);if(!field.label.trim()){setError('每个字段都需要中文显示名称。');return;}}setBusy(true);try{await request('/documents/'+encodeURIComponent(document.id),{extraction_schema:schema.map(field=>({...field,key:field.key.trim(),label:field.label.trim(),description:field.description.trim()})),expected_schema_revision:document.schema_revision || 0});await onSaved();}catch(failure){setError(failure.message);}finally{setBusy(false);}}
  return <Dialog title="自定义字段提取模板" subtitle="说明每个字段的名称、类型和含义；没有原文证据的字段必须保留 null。" wide onClose={onClose}><form onSubmit={submit}><div className="cw-modal-body"><div className="cw-schema-toolbar"><span>模板第 {document.schema_revision || 0} 版 · {schema.length} 个字段</span><Button type="button" icon={IconPlus} disabled={busy || schema.length>=30} onClick={()=>setSchema(previous=>[...previous,{key:'',label:'',type:'text',description:''}])}>增加字段</Button><Button type="button" icon={IconFlask} disabled={busy} onClick={()=>setSchema(JSON.parse(JSON.stringify(plantFieldTemplate)))}>采用常用植物模板</Button></div><div className="cw-schema-fields">{schema.map((field,index)=><section className="cw-schema-field" key={index}><header><strong>字段 {index+1}</strong><button type="button" className="cw-text-button" onClick={()=>setSchema(previous=>previous.filter((_,i)=>i!==index))} disabled={busy}><Icon as={IconTrash} size={14}/>删除</button></header><div className="cw-schema-row"><label className="cw-field"><span>key</span><input value={field.key} maxLength={64} required disabled={busy} placeholder="例如 leaf_shape" onChange={event=>update(index,{key:event.target.value})}/></label><label className="cw-field"><span>显示名称</span><input value={field.label} maxLength={100} required disabled={busy} placeholder="例如 叶形" onChange={event=>update(index,{label:event.target.value})}/></label><label className="cw-field"><span>类型</span><select value={field.type} disabled={busy} onChange={event=>update(index,{type:event.target.value})}><option value="text">文本</option><option value="number">数值</option><option value="boolean">是 / 否</option></select></label></div><label className="cw-field"><span>字段含义与提取规则</span><textarea value={field.description} rows={2} maxLength={1000} disabled={busy} placeholder="说明要提取什么、单位和限制，不要求模型补齐缺失信息。" onChange={event=>update(index,{description:event.target.value})}/></label></section>)}</div>{!schema.length && <p className="cw-quiet">当前没有字段。可以增加字段，或采用常用植物模板。</p>}<div className="cw-alert">保存模板后，下次模型整理会使用新模板。已生成产物和历史保留当时的模板，修改模板不会自动重写已有字段值。</div>{error && <div className="cw-alert error" role="alert">{error}</div>}</div><footer className="cw-modal-footer"><Button type="button" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" variant="primary" icon={IconCheck} disabled={busy}>{busy?'正在保存…':'保存字段模板'}</Button></footer></form></Dialog>;
}
function SourceMetadata({metadata}) {
  const doi=String(metadata.doi || ''), doiUrl=/^10\.\d{4,9}\/\S+$/i.test(doi)?`https://doi.org/${doi}`:null;
  const authors=Array.isArray(metadata.authors)?metadata.authors.join('、'):String(metadata.authors || '');
  if(!authors && !metadata.year && !doiUrl && !metadata.license_label && !metadata.processing_note) return null;
  return <div className="cw-source-metadata"><span><Icon as={IconBook2} size={14}/>{authors}{metadata.year?` · ${metadata.year}`:''}</span>{doiUrl && <a href={doiUrl} target="_blank" rel="noopener noreferrer">DOI: {doi}<Icon as={IconExternalLink} size={11}/></a>}{metadata.license_label && <span className="cw-source-license">{metadata.license_label}</span>}{metadata.processing_note && <details><summary>来源与处理说明</summary><p>{metadata.citation || metadata.title}</p><p>{metadata.processing_note}</p>{metadata.license_notice && <p>{metadata.license_notice}</p>}</details>}</div>;
}
