(function(){
  'use strict';
  const PAGE_SIZE = 10;
  function isServerPaginated(table){
    const root = table.closest('section, .nf-content') || document;
    return !!(table.hasAttribute('data-no-auto-pagination') || root.querySelector('.sl-pagination,.sr-pagination,[data-server-pagination]'));
  }
  function paginateTable(table){
    if (!table) return;
    if (!table.closest('[class*=\"table-wrap\"],.table-responsive,.nf-table-wrap,.nf-auto-table-scroll')) {
      const scroll=document.createElement('div'); scroll.className='nf-auto-table-scroll';
      table.parentNode.insertBefore(scroll,table); scroll.appendChild(table);
    }
    if (table.dataset.nfPaginationReady === '1' || isServerPaginated(table)) return;
    const tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows).filter(r => !r.querySelector('td[colspan]'));
    if (rows.length <= PAGE_SIZE) return;
    table.dataset.nfPaginationReady='1';
    let page=1;
    const pages=Math.ceil(rows.length/PAGE_SIZE);
    const nav=document.createElement('div');
    nav.className='nf-auto-pagination';
    nav.setAttribute('aria-label','Table pagination');
    const info=document.createElement('div'); info.className='nf-auto-pagination-info';
    const buttons=document.createElement('div'); buttons.className='nf-auto-pagination-pages';
    nav.append(info,buttons);
    const wrap=table.closest('[class*="table-wrap"],.table-responsive,.nf-table-wrap') || table;
    wrap.insertAdjacentElement('afterend',nav);
    function render(){
      rows.forEach((r,i)=>{ r.classList.toggle('nf-page-row-hidden', i < (page-1)*PAGE_SIZE || i >= page*PAGE_SIZE); });
      const start=(page-1)*PAGE_SIZE+1, end=Math.min(page*PAGE_SIZE,rows.length);
      info.textContent=`Showing ${start}–${end} of ${rows.length}`;
      buttons.innerHTML='';
      const make=(label,target,active,disabled,aria)=>{
        const b=document.createElement('button'); b.type='button'; b.className='nf-auto-page'+(active?' is-active':''); b.textContent=label;
        if(aria) b.setAttribute('aria-label',aria); b.disabled=!!disabled;
        b.addEventListener('click',()=>{page=target;render();table.scrollIntoView({block:'nearest'});}); buttons.appendChild(b);
      };
      make('‹',Math.max(1,page-1),false,page===1,'Previous page');
      let from=Math.max(1,page-2), to=Math.min(pages,from+4); from=Math.max(1,to-4);
      for(let p=from;p<=to;p++) make(String(p),p,p===page,false,`Page ${p}`);
      make('›',Math.min(pages,page+1),false,page===pages,'Next page');
    }
    render();
  }

  function normalizePageIntroOrder(){
    const content=document.querySelector('.nf-content');
    if(!content) return;
    Array.from(content.children).forEach(function(root){
      if(!root || !root.children || !root.children.length) return;
      const children=Array.from(root.children).filter(function(el){
        return el.tagName !== 'SCRIPT' && el.tagName !== 'STYLE';
      });
      if(children.length < 2) return;
      const intro=children.find(function(el){
        const cls=String(el.className || '');
        return /(^|\s)[^\s]*(?:hero|intro)(?:\s|$)/i.test(cls);
      });
      if(intro && children[0] !== intro){
        root.insertBefore(intro, children[0]);
      }
    });
  }

  function initNotifications(){
    const root=document.querySelector('.nf-admin-notifications-page');
    if(!root || root.dataset.nfTabsReady==='1') return;
    const panels=root.querySelectorAll('.an-tab-panel');
    const tabs=root.querySelectorAll('[data-an-tab]');
    if(!panels.length || !tabs.length) return;
    root.dataset.nfTabsReady='1';
    const activate=(id)=>{
      tabs.forEach(t=>{const on=t.dataset.anTab===id;t.classList.toggle('is-active',on);t.setAttribute('aria-selected',on?'true':'false');});
      panels.forEach(p=>{p.hidden=p.dataset.anPanel!==id;});
    };
    tabs.forEach(t=>t.addEventListener('click',()=>activate(t.dataset.anTab)));
    activate(root.dataset.defaultTab || 'create');
  }
  function init(){
    if(document.body && document.body.dataset.portal==='admin'){
      normalizePageIntroOrder();
      document.querySelectorAll('.nf-content table').forEach(paginateTable);
      initNotifications();
    }
  }
  window.NEFreshAdminUI={init};
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',init,{once:true}); else init();
})();
