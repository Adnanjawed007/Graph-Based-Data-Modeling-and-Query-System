import {useEffect,useRef,useState} from 'react';
import type {Core} from 'cytoscape';

type Node={id:string,type:string,identifier:string,data:Record<string,unknown>};
type Edge={id?:string,source:string,target:string,type:string};
type Payload={nodes:Node[],edges:Edge[],total_nodes?:number,total_edges?:number};
type ChatMessage={role:'assistant'|'user',content:string,resultCount?:number,status?:string,warnings?:string[],options?:string[],plan?:unknown,evidence?:unknown};
const API=import.meta.env.VITE_API_URL||'';

function richText(text:string){
  return text.split(/(\*\*.*?\*\*|\n)/g).map((part,index)=>part==='\n'?<br key={index}/>:part.startsWith('**')&&part.endsWith('**')?<strong key={index}>{part.slice(2,-2)}</strong>:part);
}

export default function App(){
  const host=useRef<HTMLDivElement>(null), cy=useRef<Core|null>(null), chatEnd=useRef<HTMLDivElement>(null);
  const [graph,setGraph]=useState<Payload>({nodes:[],edges:[]}),[stats,setStats]=useState<any>(null),[selection,setSelection]=useState<Node|null>(null);
  const [question,setQuestion]=useState(''),[messages,setMessages]=useState<ChatMessage[]>([{role:'assistant',content:'Hi! Ask me about an order, delivery, billing document, customer, or the order-to-cash flow.'}]);
  const [loading,setLoading]=useState(false),[highlighted,setHighlighted]=useState<string[]>([]),[focusedGraph,setFocusedGraph]=useState(false),[focusRoot,setFocusRoot]=useState<string|null>(null);
  const [searchText,setSearchText]=useState(''),[searchResults,setSearchResults]=useState<Node[]>([]),[relationshipFilter,setRelationshipFilter]=useState(''),[err,setErr]=useState('');

  function mergeGraph(j:any){setGraph(old=>({nodes:[...new Map([...old.nodes,...j.nodes].map((n:Node)=>[n.id,n])).values()],edges:[...new Map([...old.edges,...j.edges].map((e:Edge)=>[e.id||e.source+'|'+e.type+'|'+e.target,e])).values()],total_nodes:old.total_nodes,total_edges:old.total_edges}))}
  async function load(){try{const suffix=relationshipFilter?'&relationship_type='+encodeURIComponent(relationshipFilter):'';const [g,s]=await Promise.all([fetch(API+'/api/graph?limit=150'+suffix).then(r=>r.json()),fetch(API+'/api/dataset/stats').then(r=>r.json())]);setGraph(g);setStats(s);setFocusRoot(null);setFocusedGraph(false);setHighlighted([]);setErr('')}catch{setErr('Cannot reach the API. Refresh the page or try again shortly.')}}
  async function searchEntities(e:React.FormEvent){e.preventDefault();setErr('');try{const r=await fetch(API+'/api/entities/search?q='+encodeURIComponent(searchText)+'&limit=12');const j=await r.json();if(!r.ok)throw new Error(j.detail||'Search failed');setSearchResults(j.results)}catch(x:any){setErr(x.message)}}
  async function chooseEntity(n:Node){setSelection(n);setHighlighted([n.id]);try{const r=await fetch(API+'/api/graph/expand/'+encodeURIComponent(n.id));const j=await r.json();if(r.ok){setFocusedGraph(true);setFocusRoot(n.id);mergeGraph(j)}}catch(x:any){setErr(x.message)}}

  async function ask(e:React.FormEvent){
    e.preventDefault();const asked=question.trim();if(!asked||loading)return;
    setQuestion('');setErr('');setMessages(old=>[...old,{role:'user',content:asked}]);setLoading(true);
    try{
      const r=await fetch(API+'/api/query',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({question:asked})});
      const j=await r.json();if(!r.ok)throw new Error(j.detail||'Query failed');
      setMessages(old=>[...old,{role:'assistant',content:j.answer,resultCount:j.result_count,status:j.execution_status,warnings:j.warnings||[],options:j.options||[],plan:j.plan,evidence:j.evidence}]);
      const ev=j.evidence;
      if(ev?.nodes){
        const nodes:Node[]=ev.nodes;const edges:Edge[]=ev.edges||[];
        setGraph({nodes,edges,total_nodes:nodes.length,total_edges:edges.length});setHighlighted(j.referenced_node_ids||nodes.map(n=>n.id));setFocusRoot(ev.root||null);setFocusedGraph(true);
        if(ev.root){const rootNode=nodes.find(n=>n.id===ev.root);if(rootNode)setSelection(rootNode)}
      }
    }catch(x:any){setMessages(old=>[...old,{role:'assistant',content:`I could not complete that query: ${x.message}`}])}
    finally{setLoading(false)}
  }

  async function expand(){if(!selection)return;try{const r=await fetch(API+'/api/graph/expand/'+encodeURIComponent(selection.id));const j=await r.json();if(!r.ok)throw new Error(j.detail);mergeGraph(j)}catch(x:any){setErr(x.message)}}
  useEffect(()=>{load()},[relationshipFilter]);
  useEffect(()=>{chatEnd.current?.scrollIntoView({behavior:'smooth',block:'end'})},[messages,loading]);

  useEffect(()=>{
    if(!host.current)return;let cancelled=false;cy.current?.destroy();
    const draw=async()=>{
      const {default:cytoscape}=await import('cytoscape');if(cancelled||!host.current)return;
      const elements=[...graph.nodes.map(n=>({data:{id:n.id,label:n.type.replaceAll('_',' ')+'\n'+n.identifier,type:n.type,node:n,highlighted:highlighted.includes(n.id)}})),...graph.edges.map((e,i)=>({data:{id:e.id||'e'+i,source:e.source,target:e.target,label:e.type,highlighted:focusedGraph}}))];
      cy.current=cytoscape({container:host.current,elements,style:[
        {selector:'node',style:{'background-color':'#3d7791','label':'data(label)','color':'#172b35','font-size':'9px','text-wrap':'wrap','text-max-width':'100px','width':'34px','height':'34px','border-width':'1px','border-color':'#fff'}},
        {selector:'node[type = "customer"], node[type = "product"], node[type = "plant"], node[type = "partner_address"]',style:{'background-color':'#35a184'}},
        {selector:'node[type = "journal_entry"], node[type = "journal_entry_item"]',style:{'background-color':'#8468bd'}},
        {selector:'node[type = "sales_order"], node[type = "delivery"], node[type = "billing_document"]',style:{'background-color':'#28718a'}},
        {selector:'node[highlighted]',style:{'background-color':'#e59b36','border-width':'3px','border-color':'#9a5c00','width':'42px','height':'42px'}},
        {selector:'edge',style:{'line-color':'#a8bbc2','target-arrow-color':'#a8bbc2','target-arrow-shape':'triangle','curve-style':'bezier','label':'data(label)','font-size':'7px','width':'1px','opacity':0.42}},
        {selector:'edge[highlighted]',style:{'line-color':'#1985d0','target-arrow-color':'#1985d0','width':'3px','opacity':1,'font-size':'8px','color':'#245575','text-background-color':'#fff','text-background-opacity':0.85,'text-background-padding':'2px'}}
      ],layout:focusedGraph&&focusRoot?{name:'breadthfirst',directed:false,roots:`[id = "${focusRoot}"]`,padding:45,spacingFactor:1.15,animate:false}:{name:'cose',animate:false,fit:true,padding:35}});
      cy.current.on('tap','node',e=>setSelection(e.target.data('node')));
    };void draw();return()=>{cancelled=true;cy.current?.destroy()}
  },[graph,highlighted,focusedGraph,focusRoot]);

  const trailKinds=['sales_order','delivery','billing_document','journal_entry'];
  const trailLabels:Record<string,string>={sales_order:'Sales order',delivery:'Delivery',billing_document:'Billing document',journal_entry:'Journal entry'};
  const trail=focusRoot?trailKinds.flatMap(kind=>{const byId=new Map<string,Node>();graph.nodes.filter(n=>n.type===kind).forEach(n=>byId.set(n.identifier,n));return Array.from(byId.values()).map(n=>({kind,id:n.identifier}))}):[];

  return <main>
    <header><div><small>BUSINESS INVESTIGATION</small><h1>Order-to-Cash Context Graph</h1><p>Explore verified links across the supplied SAP records.</p></div><button onClick={load}>Refresh data</button></header>
    {err&&<div className="error">{err}</div>}
    <section className="stats">{(stats?.tables||[]).map((t:any)=><div key={t.table_name}><b>{Number(t.records).toLocaleString()}</b><span>{t.table_name.replaceAll('_',' ')}</span></div>)}</section>
    <section className="searchbar"><form onSubmit={searchEntities}><input aria-label="Search entities" value={searchText} onChange={e=>setSearchText(e.target.value)} placeholder="Find an order, customer, product, or document"/><button>Search</button></form><div className="search-results">{searchResults.map(n=><button type="button" key={n.id} onClick={()=>chooseEntity(n)}>{n.type} ? {n.identifier}</button>)}</div></section>
    <section className="workspace">
      <div className="graph"><div className="bar"><b>Relationship graph</b><select aria-label="Filter relationship type" value={relationshipFilter} onChange={e=>setRelationshipFilter(e.target.value)}><option value="">All relationship types</option><option>CONTAINS_ITEM</option><option>REFERENCES_ORDER_ITEM</option><option>REFERENCES_PRECEDING_ITEM</option><option>HAS_PRODUCT</option><option>BELONGS_TO_CUSTOMER</option><option>ACCOUNTED_BY</option><option>CLEARED_BY</option><option>HAS_STORAGE_ASSIGNMENT</option></select><span>{graph.total_nodes||0} entities / {graph.total_edges||0} links{focusedGraph?' - focused trace':' (bounded view)'}</span></div>
        {trail.length>0&&<div className="trace-strip"><span className="trace-caption">Document trail</span><div className="trace-steps">{trail.map((step,index)=><span className="trace-step" key={`${step.kind}-${step.id}`}><b>{trailLabels[step.kind]}</b><strong>{step.id}</strong>{index<trail.length-1&&<i>-&gt;</i>}</span>)}</div></div>}
        <div className="canvas" ref={host}/>
      </div>
      <aside>
        <div className="panel chat-panel"><div className="chat-heading"><div><h2>Chat with your O2C graph</h2><p>Ask questions about orders, deliveries, billing, and customers.</p></div></div>
          <div className="chat-messages" aria-live="polite">{messages.map((message,index)=><article className={`chat-message ${message.role}`} key={index}>
            <div className="message-author">{message.role==='user'?'You':'O2C Graph Assistant'}</div><div className="message-bubble"><div className="message-content">{richText(message.content)}</div>
              {message.role==='assistant'&&message.resultCount!==undefined&&<small className="message-meta">{message.resultCount} results - {message.status}</small>}
              {message.warnings?.map((w,i)=><p className="warning" key={i}>{w}</p>)}
              {message.options&&message.options.length>0&&<p className="chat-options">Possible matches: {message.options.join(', ')}</p>}
              {message.evidence!==undefined&&<details className="message-details"><summary>Query plan and retrieved evidence</summary><b>Validated plan</b><pre>{JSON.stringify(message.plan,null,2)}</pre><b>Source records</b><pre>{JSON.stringify(message.evidence,null,2)}</pre></details>}
            </div>
          </article>)}{loading&&<article className="chat-message assistant"><div className="message-author">O2C Graph Assistant</div><div className="message-bubble typing">Checking linked records...</div></article>}<div ref={chatEnd}/></div>
          <form className="chat-composer" onSubmit={ask}><textarea aria-label="Ask the dataset" value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();e.currentTarget.form?.requestSubmit()}}} placeholder="Ask a question..."/><div><small>Press Enter to send | Shift+Enter for a new line</small><button disabled={loading||!question.trim()}>{loading?'Investigating...':'Send'}</button></div></form>
        </div>
        <div className="panel selected-panel"><h2>Selected entity</h2>{selection?<><div className="badge">{selection.type}</div><h3>{selection.identifier}</h3><button onClick={expand}>Expand neighbors</button><pre>{JSON.stringify(selection.data,null,2)}</pre></>:<p>Select a graph node to inspect source fields.</p>}</div>
      </aside>
    </section>
    <footer>Answers are grounded in the supplied records. Expand a chat response to inspect its validated query plan and source evidence.</footer>
  </main>
}
