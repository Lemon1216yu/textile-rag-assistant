import {useEffect, useState} from 'react';
import {ArrowUpRight, Check, ChevronRight, CircleAlert, KeyRound, Loader2, MessageSquare, Network, SlidersHorizontal, Workflow} from 'lucide-react';

type Group='chat'|'embedding'|'rerank';
type Snapshot={revision:string;source:string;groups:Record<Group,Record<string,any>>};
type Api=(path:string,options?:RequestInit)=>Promise<any>;
const definitions={
 chat:{title:'对话模型',subtitle:'意图识别、知识回答与历史摘要',icon:MessageSquare,model:'llm_model',endpoint:'llm_base',placeholder:'qwen3.5-flash'},
 embedding:{title:'Embedding',subtitle:'将资料与问题转换为语义向量',icon:Network,model:'embedding_model',endpoint:'embedding_base',placeholder:'text-embedding-v3'},
 rerank:{title:'Rerank',subtitle:'对召回的知识片段进行精排',icon:Workflow,model:'rerank_model',endpoint:'rerank_url',placeholder:'gte-rerank-v2'},
};

export default function ModelSettings({api,onSaved}:{api:Api;onSaved:()=>Promise<void>}){
 const [snapshot,setSnapshot]=useState<Snapshot|null>(null),[group,setGroup]=useState<Group>('chat'),[draft,setDraft]=useState<Record<string,any>>({}),[secret,setSecret]=useState(''),[clearKey,setClearKey]=useState(false),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[test,setTest]=useState<{ok:boolean;message:string;latency_ms:number}|null>(null),[advanced,setAdvanced]=useState(false);
 const definition=definitions[group];
 const dirty=!!snapshot&&(JSON.stringify(draft)!==JSON.stringify(snapshot.groups[group])||!!secret||clearKey);
 async function refresh(){try{const result=await api('/admin/models');setSnapshot(result);setDraft({...result.groups[group]});setError('');setSecret('');setClearKey(false);setTest(null)}catch(e){setError((e as Error).message)}}
 useEffect(()=>{void refresh()},[]);
 useEffect(()=>{const leave=(e:BeforeUnloadEvent)=>{if(dirty){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',leave);return()=>window.removeEventListener('beforeunload',leave)},[dirty]);
 function update(key:string,value:unknown){setDraft(d=>({...d,[key]:value}));setTest(null);setNotice('')}
 function choose(next:Group){if(next===group)return;if(dirty){setError('当前有未保存的修改，请先保存或点击“放弃修改”后切换。');return}setGroup(next);setDraft({...snapshot!.groups[next]});setSecret('');setClearKey(false);setTest(null);setError('');setNotice('');setAdvanced(false)}
 function preset(name:string){setTest(null);setNotice('');if(name==='bailian'){const changes=group==='chat'?{llm_base:'https://dashscope.aliyuncs.com/compatible-mode/v1',llm_model:'qwen3.5-flash',llm_json_mode:true}:group==='embedding'?{embedding_base:'https://dashscope.aliyuncs.com/compatible-mode/v1',embedding_model:'text-embedding-v3',embedding_dimension:1024}:{rerank_url:'',rerank_model:'qwen3-rerank',rerank_format:'compatible'};setDraft(d=>({...d,...changes}))}else{update(definition.endpoint,'');update(definition.model,'');if(group==='rerank')update('rerank_format','compatible')}}
 async function submit(action:'test'|'save'){
   if(!snapshot)return;setBusy(action);setError('');setNotice('');
   const {key_configured,...values}=draft;
   try{
     const payload={values,revision:snapshot.revision,key_action:clearKey?'clear':secret?'replace':'keep',api_key:secret};
     if(action==='test'){setTest(null);setTest(await api(`/admin/models/${group}/test`,{method:'POST',body:JSON.stringify(payload)}))}
     else{const saved=await api(`/admin/models/${group}`,{method:'PUT',body:JSON.stringify(payload)});setSnapshot(saved);setDraft({...saved.groups[group]});setSecret('');setClearKey(false);setTest(null);setNotice('配置已保存，将用于新的请求和索引任务。');await onSaved()}
   }catch(e){setError((e as Error).message)}finally{setBusy('')}
 }
 if(!snapshot)return <div className="model-loading">{error?<><CircleAlert size={20}/><p role="alert">{error}</p><button className="secondary" onClick={()=>void refresh()}>重试</button></>:<><Loader2 className="spin" size={20}/>正在读取模型配置</>}</div>;
 const Icon=definition.icon;
 return <div className="model-settings">
  <div className="model-settings-intro"><div><h2>模型与服务</h2><p>连接你的模型服务，为整个工作空间设置默认模型。</p></div><span className="model-scope"><KeyRound size={13}/>仅管理员可修改</span></div>
  <div className="model-layout"><nav className="model-nav" aria-label="模型用途">{(Object.keys(definitions) as Group[]).map(key=>{const item=definitions[key],Glyph=item.icon;return <button key={key} className={group===key?'selected':''} onClick={()=>choose(key)} disabled={!!busy} aria-current={group===key?'page':undefined}><Glyph size={17}/><span><strong>{item.title}</strong><small>{snapshot.groups[key].key_configured?'已配置':'未配置'}</small></span><ChevronRight size={14}/></button>})}<p>设置对所有成员生效。<br/>正在处理的请求保持原配置。</p></nav>
  <form className="model-detail" onSubmit={e=>{e.preventDefault();void submit('save')}}>
   <div className="model-detail-heading"><div className="model-glyph"><Icon size={21}/></div><div><h3>{definition.title}</h3><p>{definition.subtitle}</p></div><span className={'model-state '+(draft.key_configured?'configured':'')}>{draft.key_configured?'密钥已保存':'等待配置'}</span></div>
   <div className="model-presets"><span>快速填写</span><button type="button" onClick={()=>preset('bailian')} disabled={!!busy}>阿里云百炼</button><button type="button" onClick={()=>preset('custom')} disabled={!!busy}>自定义兼容服务 <ArrowUpRight size={12}/></button></div>
   <fieldset disabled={!!busy}>
   <label className="model-row"><span><strong>模型名称</strong><small>填写服务商提供的准确 Model ID</small></span><input value={draft[definition.model]||''} placeholder={definition.placeholder} onChange={e=>update(definition.model,e.target.value)} required maxLength={200} autoComplete="off"/></label>
   <label className="model-row"><span><strong>{group==='rerank'?'接口 URL':'Base URL'}</strong><small>{group==='rerank'?'包含重排请求路径的完整地址':'OpenAI Chat Completions / Embeddings 兼容地址'}</small></span><input type="url" value={draft[definition.endpoint]||''} placeholder={group==='rerank'?'https://workspace-id.cn-beijing.maas.aliyuncs.com/compatible-api/v1/reranks':'https://api.example.com/v1'} onChange={e=>update(definition.endpoint,e.target.value)} required maxLength={500} autoComplete="off"/></label>
   <div className="model-row"><span><strong><label htmlFor="model-api-key">API Key</label></strong><small>{draft.key_configured?'留空保留现有密钥。保存后不回显。':'只提交到本工作空间服务器，不保存在浏览器。'}</small></span><div className="model-key"><input id="model-api-key" type="password" value={secret} placeholder={draft.key_configured?'已配置 · 输入可替换':'输入 API Key'} autoComplete="new-password" spellCheck={false} disabled={clearKey||!!busy} onChange={e=>{setSecret(e.target.value);setTest(null);setNotice('')}}/><label className="model-checkbox"><input type="checkbox" checked={clearKey} onChange={e=>{setClearKey(e.target.checked);setSecret('');setTest(null)}}/>清除已保存密钥</label></div></div>
   {group==='embedding'&&<><label className="model-row"><span><strong>向量维度</strong><small>必须与模型输出和索引维度一致</small></span><input type="number" min={1} max={8192} value={draft.embedding_dimension} onChange={e=>update('embedding_dimension',Number(e.target.value))}/></label><div className="model-hint"><CircleAlert size={16}/><p>已有向量索引时，不能直接更换模型、地址或维度。首次启用后，原文检索模式下导入的旧资料需要上传新版本，才能生成向量。</p></div></>}
   {group==='rerank'&&<p className="model-footnote">百炼重排地址与业务空间及模型有关，请从控制台复制完整 URL。快速填写只选择协议与模型，不猜测你的业务空间地址。</p>}
   {group==='rerank'&&<label className="model-row"><span><strong>请求协议</strong><small>不同服务的请求结构可能不同</small></span><select value={draft.rerank_format} onChange={e=>update('rerank_format',e.target.value)}><option value="dashscope">百炼原生 · input / parameters</option><option value="compatible">兼容格式 · query / documents</option></select></label>}
   {group==='chat'&&<><button className="model-advanced-toggle" type="button" onClick={()=>setAdvanced(!advanced)} aria-expanded={advanced}><SlidersHorizontal size={15}/>高级参数<ChevronRight size={14} style={{transform:advanced?'rotate(90deg)':''}}/></button>{advanced&&<div className="model-advanced"><label>Temperature<input type="number" min={0} max={2} step={0.1} value={draft.llm_temperature} onChange={e=>update('llm_temperature',Number(e.target.value))}/><small>较低值适合基于资料回答</small></label><label>最大输出 Token<input type="number" min={128} max={16384} value={draft.llm_max_tokens} onChange={e=>update('llm_max_tokens',Number(e.target.value))}/></label><label>请求超时（秒）<input type="number" min={5} max={60} value={draft.model_timeout} onChange={e=>update('model_timeout',Number(e.target.value))}/></label><label className="model-checkbox"><input type="checkbox" checked={draft.llm_json_mode} onChange={e=>update('llm_json_mode',e.target.checked)}/>发送 JSON 模式参数<small>不支持 response_format 的服务可关闭；路由仍需输出 JSON。</small></label></div>}</>}
   </fieldset>
   {error&&<div className="model-message error" role="alert"><CircleAlert size={16}/><span>{error}</span></div>}
   {notice&&<div className="model-message success" role="status"><Check size={16}/>{notice}</div>}
   {test&&<div className={'model-message '+(test.ok?'success':'error')} role="status">{test.ok?<Check size={16}/>:<CircleAlert size={16}/>}<div><strong>{test.ok?'连接测试通过':'连接测试未通过'} · {test.latency_ms} ms</strong><p>{test.message}</p></div></div>}
   <div className="model-footer"><span>{dirty?'有未保存的修改':snapshot.source==='workspace'?'使用工作空间配置':'当前继承服务器配置'}</span><div>{dirty&&<button type="button" className="subtle-button" disabled={!!busy} onClick={()=>void refresh()}>放弃修改</button>}<button type="button" className="secondary" disabled={!!busy||clearKey} onClick={()=>void submit('test')}>{busy==='test'&&<Loader2 size={15} className="spin"/>}测试连接</button><button className="primary" disabled={!!busy||!dirty}>{busy==='save'&&<Loader2 size={15} className="spin"/>}保存配置</button></div></div>
   <p className="model-footnote">连接测试只发送合成文本，可能产生少量 API 费用。测试不会保存设置。保存后新请求立即生效，无需重启服务。</p>
  </form></div>
 </div>
}
