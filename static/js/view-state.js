/* NE LOCALS global view-state contract
   Normal navigation/reload/back-forward starts at the top.
   Only an explicit form/data action may restore its prior page position once. */
(function(){
  'use strict';

  var ACTION_KEY='nefresh:action-view:v1';
  var LEGACY_PREFIX='nefresh:view-state:v2:';
  var LEGACY_STORE_PREFIX='neFreshStoreViewState:';
  var ACTION_TTL_MS=1000*60*3;
  var READY_CLASS='nf-view-ready';
  var PENDING_CLASS='nf-action-restore-pending';
  var TOP_THRESHOLD=420;

  function now(){return Date.now?Date.now():(new Date()).getTime();}
  function safeGet(key){try{return window.sessionStorage?sessionStorage.getItem(key):null;}catch(_){return null;}}
  function safeSet(key,value){try{if(window.sessionStorage)sessionStorage.setItem(key,value);}catch(_){}}
  function safeRemove(key){try{if(window.sessionStorage)sessionStorage.removeItem(key);}catch(_){}}

  function clearLegacyViewState(){
    try{
      if(!window.sessionStorage)return;
      var keys=[];
      for(var i=0;i<sessionStorage.length;i+=1){
        var key=sessionStorage.key(i);
        if(!key)continue;
        if(key.indexOf(LEGACY_PREFIX)===0||key.indexOf(LEGACY_STORE_PREFIX)===0)keys.push(key);
      }
      keys.forEach(function(key){sessionStorage.removeItem(key);});
    }catch(_){}
  }

  function parseActionState(){
    var raw=safeGet(ACTION_KEY);
    if(!raw)return null;
    try{
      var state=JSON.parse(raw);
      if(!state||!state.t||!state.path){safeRemove(ACTION_KEY);return null;}
      if((now()-Number(state.t))>ACTION_TTL_MS){safeRemove(ACTION_KEY);return null;}
      return state;
    }catch(_){safeRemove(ACTION_KEY);return null;}
  }

  function absoluteTop(el){
    if(!el||!el.getBoundingClientRect)return 0;
    return el.getBoundingClientRect().top+(window.pageYOffset||0);
  }

  function anchorForForm(form){
    if(!form||!form.closest)return null;
    var anchor=form.closest('[data-action-view-anchor], [id]');
    if(!anchor||!anchor.id)return null;
    return {id:anchor.id,offset:Math.round((window.pageYOffset||0)-absoluteTop(anchor))};
  }

  function rememberAction(form){
    if(form&&form.closest&&form.closest('[data-view-state-managed="page"]'))return;
    if(form&&form.matches&&form.matches('[data-no-view-persist]'))return;
    var anchor=anchorForForm(form);
    safeSet(ACTION_KEY,JSON.stringify({
      t:now(),
      path:window.location.pathname,
      y:Math.max(0,Math.round(window.pageYOffset||document.documentElement.scrollTop||0)),
      anchorId:anchor?anchor.id:'',
      anchorOffset:anchor?anchor.offset:0
    }));
  }

  function restoreTargetY(state){
    if(state&&state.anchorId){
      var anchor=document.getElementById(state.anchorId);
      if(anchor)return Math.max(0,Math.round(absoluteTop(anchor)+Number(state.anchorOffset||0)));
    }
    return Math.max(0,Math.round(Number(state&&state.y||0)));
  }

  function reveal(){
    document.documentElement.classList.remove(PENDING_CLASS,'nf-view-boot','nf-restore-pending');
    document.documentElement.classList.add(READY_CLASS);
  }

  function waitForStylesBeforeReveal(callback){
    var links=Array.prototype.slice.call(document.querySelectorAll('head link[rel="stylesheet"]'));
    var pending=links.filter(function(link){
      try{return !link.sheet;}catch(_){return true;}
    });
    var finished=false;
    var remaining=pending.length;
    function done(){
      if(finished)return;
      finished=true;
      requestAnimationFrame(function(){requestAnimationFrame(callback);});
    }
    if(!remaining){done();return;}
    function settle(){remaining-=1;if(remaining<=0)done();}
    pending.forEach(function(link){
      link.addEventListener('load',settle,{once:true});
      link.addEventListener('error',settle,{once:true});
    });
    window.setTimeout(done,5000);
  }

  function scrollTopNow(){
    try{window.scrollTo({top:0,left:0,behavior:'auto'});}catch(_){window.scrollTo(0,0);}
  }

  function scrollToExplicitHash(){
    var hash=window.location.hash||'';
    if(!hash||hash==='#')return false;
    var target=null;
    try{target=document.querySelector(hash);}catch(_){target=null;}
    if(!target)return false;
    var top=absoluteTop(target);
    var header=document.querySelector('.nf-topbar, .header-strip, header, .navbar');
    var offset=header?header.offsetHeight+12:12;
    try{window.scrollTo({top:Math.max(0,top-offset),left:0,behavior:'auto'});}catch(_){window.scrollTo(0,Math.max(0,top-offset));}
    return true;
  }

  function restoreOneShotAction(state){
    if(!state||state.path!==window.location.pathname)return false;
    safeRemove(ACTION_KEY);
    var apply=function(){
      var y=restoreTargetY(state);
      try{window.scrollTo({top:y,left:0,behavior:'auto'});}catch(_){window.scrollTo(0,y);}
    };
    requestAnimationFrame(function(){
      apply();
      requestAnimationFrame(function(){apply();reveal();});
    });
    window.setTimeout(function(){apply();reveal();},140);
    return true;
  }

  function initialisePagePosition(){
    clearLegacyViewState();
    var actionState=parseActionState();
    if(actionState&&actionState.path===window.location.pathname){restoreOneShotAction(actionState);return;}
    if(window.location.hash&&scrollToExplicitHash()){reveal();return;}
    scrollTopNow();
    reveal();
  }

  function initialiseScrollTopButton(){
    var existingLocal=document.querySelector('#backToTop, #termsBackToTop, #privacyBackToTop, #securityBackToTop, #helpBackToTop, .back-to-top, [data-back-to-top]');
    if(existingLocal)return;
    var button=document.createElement('button');
    button.type='button';
    button.className='nf-global-scroll-top';
    button.setAttribute('aria-label','Scroll to top');
    button.setAttribute('title','Back to top');
    button.innerHTML='<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    document.body.appendChild(button);
    var raf=null;
    function update(){raf=null;var y=window.pageYOffset||document.documentElement.scrollTop||0;button.classList.toggle('is-visible',y>TOP_THRESHOLD);}
    function schedule(){if(raf)return;raf=requestAnimationFrame(update);}
    button.addEventListener('click',function(){
      var reduced=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      try{window.scrollTo({top:0,left:0,behavior:reduced?'auto':'smooth'});}catch(_){window.scrollTo(0,0);}
    });
    window.addEventListener('scroll',schedule,{passive:true});
    window.addEventListener('resize',schedule,{passive:true});
    update();
  }

  try{if('scrollRestoration'in history)history.scrollRestoration='manual';}catch(_){}

  document.addEventListener('submit',function(event){
    if(event.defaultPrevented)return;
    var form=event.target;
    if(!form||String(form.tagName||'').toLowerCase()!=='form')return;
    rememberAction(form);
  },false);

  document.addEventListener('click',function(event){
    var trigger=event.target&&event.target.closest?event.target.closest('[data-action-view-persist]'):null;
    if(!trigger)return;
    rememberAction(trigger.closest?trigger.closest('form'):null);
  },false);

  window.NEFreshViewState={rememberAction:rememberAction,clearAction:function(){safeRemove(ACTION_KEY);},scrollTop:scrollTopNow};

  function bootReady(){
    waitForStylesBeforeReveal(function(){
      initialisePagePosition();
      initialiseScrollTopButton();
    });
  }

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',bootReady,{once:true});
  }else{
    bootReady();
  }

  window.addEventListener('pageshow',function(event){
    if(!event.persisted)return;
    var actionState=parseActionState();
    if(actionState&&actionState.path===window.location.pathname){restoreOneShotAction(actionState);return;}
    if(!window.location.hash)scrollTopNow();
  });
})();
