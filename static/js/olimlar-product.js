(function () {
 'use strict';
 function init() {
  var cabinetMenu=document.querySelector('.cab-menu-toggle');
  if(cabinetMenu)cabinetMenu.addEventListener('click',function(){var open=cabinetMenu.getAttribute('aria-expanded')!=='true';cabinetMenu.setAttribute('aria-expanded',String(open));cabinetMenu.closest('.cab-side').classList.toggle('is-menu-open',open);});
  var form=document.getElementById('hx-search-form');
  if(form){
   document.querySelectorAll('[data-search-action]').forEach(function(button){button.addEventListener('click',function(){
    form.action=button.dataset.searchAction;
    document.querySelectorAll('[data-search-action]').forEach(function(b){b.setAttribute('aria-pressed',String(b===button));});
    document.getElementById('home-search-hint').textContent=button.dataset.searchHint;
    document.getElementById('hx-search-input').focus();
   });});
   form.addEventListener('submit',function(){goal('hero_search_submit');});
  }
  document.querySelectorAll('[data-goal]').forEach(function(el){el.addEventListener('click',function(){goal(el.dataset.goal);});});
  var query=new URLSearchParams(location.search).get('q');
  var universitySearch=document.getElementById('uniSearch');
  if(query&&universitySearch){universitySearch.value=query;universitySearch.dispatchEvent(new Event('input',{bubbles:true}));}
  document.querySelectorAll('input[type="password"]').forEach(function(input){
   if(input.closest('.ol-password')||document.getElementById('toggle-pw'))return;
   var wrap=document.createElement('div');wrap.className='ol-password';input.before(wrap);wrap.append(input);
   var button=document.createElement('button');button.type='button';button.className='ol-icon-btn';button.setAttribute('aria-label','Parolni ko‘rsatish');button.setAttribute('aria-pressed','false');button.innerHTML='<i class="bi bi-eye" aria-hidden="true"></i>';wrap.append(button);
   button.addEventListener('click',function(){var show=input.type==='password';input.type=show?'text':'password';button.setAttribute('aria-pressed',String(show));button.setAttribute('aria-label',show?'Parolni yashirish':'Parolni ko‘rsatish');button.firstChild.className=show?'bi bi-eye-slash':'bi bi-eye';});
  });
  document.querySelectorAll('.gr-filterbar,.uni-filters').forEach(makeFilterDrawer);
  loadHomeAnalytics();
  enhanceTables(document);
  var main=document.getElementById('main-content');
  if(main)new MutationObserver(function(records){if(records.some(function(r){return r.addedNodes.length;}))enhanceTables(main);}).observe(main,{childList:true,subtree:true});
 }
 function loadHomeAnalytics(){
  var years=document.getElementById('home-year-chart'),institutions=document.getElementById('home-institution-chart');
  if(!years||!institutions)return;
  [years,institutions].forEach(function(el){el.setAttribute('aria-busy','true');});
  fetch('/analytics-data').then(function(response){if(!response.ok)throw new Error('Unavailable');return response.json();}).then(function(data){
   var totals={};(data.trend||[]).forEach(function(item){var year=String(item.period).slice(0,4);if(/^\d{4}$/.test(year))totals[year]=(totals[year]||0)+Number(item.count||0);});
   renderBars(years,Object.keys(totals).sort().slice(-6).map(function(year){return {label:year,count:totals[year]};}));
   renderBars(institutions,(data.top_muassasalar||[]).slice(0,5).map(function(item){return {label:item.muassasa,count:Number(item.count||0),href:'/university/'+encodeURIComponent(item.muassasa)};}));
  }).catch(function(){[years,institutions].forEach(function(el){el.replaceChildren();var text=document.createElement('p');text.className='ol-chart__state--error';text.textContent='Ma’lumotni yuklab bo‘lmadi.';var button=document.createElement('button');button.type='button';button.className='ol-btn ol-btn--secondary';button.textContent='Qayta urinish';button.addEventListener('click',loadHomeAnalytics);el.append(text,button);});}).finally(function(){[years,institutions].forEach(function(el){el.setAttribute('aria-busy','false');});});
 }
 function renderBars(container,items){
  container.replaceChildren();if(!items.length){var message=document.createElement('p');message.className='ol-chart__state';message.textContent='Bu kesimda ma’lumot topilmadi.';container.append(message);return;}
  var max=Math.max.apply(null,items.map(function(item){return item.count;}))||1;
  var list=document.createElement('ol');list.className='ol-data-bars';
  items.forEach(function(item){var row=document.createElement('li');var label=document.createElement(item.href?'a':'span');label.textContent=item.label;if(item.href)label.href=item.href;var count=document.createElement('strong');count.textContent=item.count.toLocaleString('uz-UZ');var bar=document.createElement('span');bar.className='ol-data-bars__bar';bar.style.width=Math.max(1,item.count/max*100)+'%';bar.setAttribute('aria-hidden','true');row.append(label,count,bar);list.append(row);});container.append(list);
 }
 function goal(name){try{if(typeof window.ym==='function')window.ym(110348117,'reachGoal',name);}catch(e){}}
 function enhanceTables(root){
  root.querySelectorAll('table').forEach(function(table){
   if(table.closest('.heatmap-wrapper')||table.classList.contains('heatmap-table'))return;
   var headers=Array.from(table.querySelectorAll('thead tr:first-child th')).map(function(th){return th.textContent.trim();});
   if(!headers.length)return;
   table.classList.add('ol-responsive-table');
   table.querySelectorAll('tbody tr').forEach(function(row){
    var cells=Array.from(row.children);if(cells.length!==headers.length||cells.some(function(c){return c.colSpan>1;}))return;
    row.classList.add('ol-responsive-row');cells.forEach(function(cell,i){if(headers[i])cell.dataset.label=headers[i];});
   });
  });
 }
 function makeFilterDrawer(panel,index){
  var launch=document.createElement('button');launch.type='button';launch.className='ol-btn ol-btn--secondary ol-filter-launch';launch.innerHTML='<i class="bi bi-sliders" aria-hidden="true"></i> Filtrlar';
  panel.id=panel.id||'ol-product-filters-'+index;launch.setAttribute('aria-controls',panel.id);launch.setAttribute('aria-expanded','false');panel.before(launch);panel.classList.add('ol-mobile-filters');
  var close=document.createElement('button');close.type='button';close.className='ol-btn ol-btn--secondary ol-filter-close';close.textContent='Natijalarga qaytish';panel.prepend(close);
  var mobile=window.matchMedia('(max-width:767px)');var previousOverflow='';
  function toggle(open){
   panel.dataset.open=String(open);launch.setAttribute('aria-expanded',String(open));
   if(open){panel.setAttribute('role','dialog');panel.setAttribute('aria-modal','true');panel.setAttribute('aria-label','Qidiruv filtrlari');previousOverflow=document.body.style.overflow;document.body.style.overflow='hidden';close.focus();}
   else{panel.removeAttribute('role');panel.removeAttribute('aria-modal');document.body.style.overflow=previousOverflow;if(mobile.matches)launch.focus();}
  }
  launch.addEventListener('click',function(){toggle(true);});close.addEventListener('click',function(){toggle(false);});
  panel.addEventListener('keydown',function(e){
   if(!mobile.matches||panel.dataset.open!=='true')return;
   if(e.key==='Escape'){e.preventDefault();toggle(false);}
   if(e.key==='Tab'){var items=Array.from(panel.querySelectorAll('a,button,input,select,textarea,[tabindex="0"]')).filter(function(el){return !el.disabled&&el.getClientRects().length;});var first=items[0],last=items[items.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
  });
  mobile.addEventListener('change',function(){if(!mobile.matches&&panel.dataset.open==='true')toggle(false);});
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
