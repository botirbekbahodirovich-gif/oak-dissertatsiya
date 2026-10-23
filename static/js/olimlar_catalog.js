/* Olimlar catalogue v3 */
(function () {
  'use strict';
  var state = {q:'',ixtisoslik:[],viloyat:[],muassasa:'',daraja:'',claimed:'',sort:'students',view:'list',page:1};
  var els = {}, requestSequence = 0, activeController = null, searchTimer = null, lastFilterButton = null;
  var COLORS = ['var(--color-text-secondary)'];

  function byId(id){ return document.getElementById(id); }
  function escapeHtml(value){ var d=document.createElement('div'); d.textContent=value==null?'':String(value); return d.innerHTML; }
  function formatNumber(value){ return Number(value||0).toLocaleString('uz-UZ'); }
  function splitParam(value){ return (value||'').split(',').map(function(v){return v.trim();}).filter(Boolean); }

  function readURL(){
    var p=new URLSearchParams(location.search);
    state.q=p.get('q')||'';
    state.ixtisoslik=splitParam(p.get('ixtisoslik'));
    state.viloyat=splitParam(p.get('viloyat'));
    state.muassasa=p.get('muassasa')||'';
    state.daraja=p.get('daraja')||'';
    state.claimed=p.get('claimed')||'';
    state.sort=p.get('sort')==='name'?'name':'students';
    state.view=p.get('view')==='grouped'?'grouped':'list';
    state.page=Math.max(1,parseInt(p.get('page'),10)||1);
  }
  function urlParams(includePage){
    var p=new URLSearchParams();
    if(state.q)p.set('q',state.q);
    if(state.ixtisoslik.length)p.set('ixtisoslik',state.ixtisoslik.join(','));
    if(state.viloyat.length)p.set('viloyat',state.viloyat.join(','));
    if(state.muassasa)p.set('muassasa',state.muassasa);
    if(state.daraja)p.set('daraja',state.daraja);
    if(state.claimed)p.set('claimed','1');
    if(state.sort!=='students')p.set('sort',state.sort);
    if(state.view!=='list')p.set('view',state.view);
    if(includePage!==false&&state.page>1)p.set('page',state.page);
    return p;
  }
  function syncURL(){ var query=urlParams(true).toString(); history.replaceState(null,'',location.pathname+(query?'?'+query:'')); }
  function avatarMarkup(s){
    if(s.photo_url)return '<img class="oc-av" src="'+escapeHtml(s.photo_url)+'" alt="'+escapeHtml(s.display)+'" loading="lazy">';
    var name=s.display||'?'; var hash=0;
    for(var i=0;i<name.length;i++)hash=name.charCodeAt(i)+((hash<<5)-hash);
    return '<div class="oc-av" style="background:'+COLORS[Math.abs(hash)%COLORS.length]+'" aria-hidden="true">'+escapeHtml(name.trim().charAt(0).toUpperCase()||'?')+'</div>';
  }
  function fallbackAvatar(img){
    var name=img.alt||'?'; var hash=0;
    for(var i=0;i<name.length;i++)hash=name.charCodeAt(i)+((hash<<5)-hash);
    var d=document.createElement('div'); d.className='oc-av'; d.style.background=COLORS[Math.abs(hash)%COLORS.length];
    d.setAttribute('aria-hidden','true'); d.textContent=name.trim().charAt(0).toUpperCase()||'?'; img.replaceWith(d);
  }
  function bindImages(root){ root.querySelectorAll('img.oc-av').forEach(function(img){img.addEventListener('error',function(){fallbackAvatar(img);},{once:true});}); }
  function scholarRow(s){
    var badges='';
    if(s.has_dsc)badges+='<span class="oc-b dsc">DSc</span>';
    if(s.has_phd)badges+='<span class="oc-b phd">PhD</span>';
    var verified=s.is_claimed?'<i class="bi bi-patch-check-fill oc-verified" title="Tasdiqlangan profil" aria-label="Tasdiqlangan profil"></i>':'';
    var institution=s.institutions&&s.institutions[0]?escapeHtml(s.institutions[0]):'Muassasa ko‘rsatilmagan';
    var specs=(s.specialties||[]).map(function(item){
      var full=item.code+(item.name?' - '+item.name:'');
      return '<span class="oc-specialty" title="'+escapeHtml(full)+'" aria-label="'+escapeHtml(full)+'">'+escapeHtml(item.code)+'</span>';
    }).join('');
    return '<article class="oc-card">'+avatarMarkup(s)+
      '<div class="oc-card-body"><div class="oc-name-line"><h3 class="oc-card-name">'+escapeHtml(s.display)+'</h3>'+verified+
      '<div class="oc-badges">'+badges+'</div></div><div class="oc-institution" title="'+institution+'">'+institution+'</div>'+
      '<div class="oc-specialties">'+specs+'</div></div>'+
      '<div class="oc-student-metric"><strong>'+formatNumber(s.total_students)+'</strong><span>shogird</span></div>'+
      '<a class="oc-profile-link" href="/olim/'+encodeURIComponent(s.name)+'">Profilni ko‘rish</a></article>';
  }
  function emptyState(){
    var active=[];
    if(state.q)active.push('qidiruv: '+state.q);
    active=active.concat(state.ixtisoslik,state.viloyat);
    if(state.muassasa)active.push(state.muassasa);
    if(state.daraja)active.push(state.daraja.toUpperCase());
    if(state.claimed)active.push('tasdiqlangan profil');
    return '<section class="oc-empty"><i class="bi bi-search" aria-hidden="true"></i><h2>Hech narsa topilmadi</h2>'+
      '<p>'+(active.length?'Faol qidiruv va filtrlar: '+escapeHtml(active.join(', '))+'. ':'')+
      'Yozuvni tekshiring yoki filtrlarni tozalab qayta urinib ko‘ring.</p>'+
      '<button type="button" class="oc-action" data-clear-all>Filtrlarni tozalash</button></section>';
  }
  function errorState(){
    return '<section class="oc-error"><i class="bi bi-cloud-slash" aria-hidden="true"></i><h2>Natijalarni yuklab bo‘lmadi</h2>'+
      '<p>Internet aloqasini tekshiring va qayta urinib ko‘ring.</p><button type="button" class="oc-action" data-retry>Qayta urinish</button></section>';
  }
  function loadingState(){
    return '<div class="oc-skeleton" aria-label="Natijalar yuklanmoqda"><div class="oc-skeleton-row"></div><div class="oc-skeleton-row"></div><div class="oc-skeleton-row"></div></div>';
  }
  function renderList(data){
    if(!data.scholars.length){els.content.innerHTML=emptyState();bindStateActions();return;}
    els.content.innerHTML='<div class="oc-list">'+data.scholars.map(scholarRow).join('')+'</div>'; bindImages(els.content);
  }
  function renderGroups(data){
    if(!data.groups.length){els.content.innerHTML=emptyState();bindStateActions();return;}
    els.content.innerHTML='<div class="oc-group-list">'+data.groups.map(function(group,index){
      var open=index===0; var bodyId='oc-group-'+group.key;
      return '<section class="oc-group"><button type="button" class="oc-group-toggle" aria-expanded="'+open+'" aria-controls="'+bodyId+'" data-group-toggle>'+
        '<span class="oc-group-label">'+escapeHtml(group.label)+'</span><span class="oc-group-count">'+formatNumber(group.scholar_count)+' ta olim</span>'+
        '<i class="bi bi-chevron-down" aria-hidden="true"></i></button><div class="oc-group-body" id="'+bodyId+'" '+(open?'':'hidden')+'>'+
        '<div class="oc-group-list">'+group.scholars.map(scholarRow).join('')+'</div>'+
        (group.has_more?'<button type="button" class="oc-more" data-more="'+escapeHtml(group.key)+'" data-page="2">Yana '+formatNumber(group.total_in_group-group.scholars.length)+' ta</button>':'')+
        '</div></section>';
    }).join('')+'</div>';
    bindImages(els.content); bindGroups();
  }
  function bindStateActions(){
    var clear=els.content.querySelector('[data-clear-all]'); if(clear)clear.addEventListener('click',clearAll);
    var retry=els.content.querySelector('[data-retry]'); if(retry)retry.addEventListener('click',load);
  }
  function bindGroups(){
    els.content.querySelectorAll('[data-group-toggle]').forEach(function(button){
      button.addEventListener('click',function(){
        var body=byId(button.getAttribute('aria-controls')); var expanded=button.getAttribute('aria-expanded')==='true';
        button.setAttribute('aria-expanded',String(!expanded)); body.hidden=expanded;
      });
    });
    els.content.querySelectorAll('[data-more]').forEach(function(button){
      button.addEventListener('click',function(){
        var params=urlParams(false); params.set('key',button.dataset.more); params.set('gpage',button.dataset.page);
        button.disabled=true; button.textContent='Yuklanmoqda…';
        fetch('/api/olimlar/group?'+params.toString()).then(function(response){
          if(!response.ok)throw new Error('HTTP '+response.status); return response.json();
        }).then(function(data){
          if(!data.ok)throw new Error(data.error||'API');
          var list=button.parentElement.querySelector('.oc-group-list');
          list.insertAdjacentHTML('beforeend',data.scholars.map(scholarRow).join('')); bindImages(list);
          if(data.has_more){button.disabled=false;button.dataset.page=String(Number(button.dataset.page)+1);button.textContent='Yana yuklash';}
          else button.remove();
        }).catch(function(){button.disabled=false;button.textContent='Qayta urinish';});
      });
    });
  }
  function renderRanking(items){
    if(!items||!items.length){els.ranking.hidden=true;els.rankingList.innerHTML='';return;}
    els.rankingList.innerHTML=items.map(function(s,index){
      return '<div class="oc-ranking-item"><span class="n">'+(index+1)+'</span><a href="/olim/'+encodeURIComponent(s.name)+'">'+escapeHtml(s.display)+'</a><strong>'+formatNumber(s.total_students)+'</strong></div>';
    }).join(''); els.ranking.hidden=false;
  }
  function renderPager(page,pages){
    if(pages<=1){els.pager.innerHTML='';return;}
    var html='<button type="button" data-page="'+(page-1)+'" '+(page<=1?'disabled':'')+' aria-label="Oldingi sahifa"><i class="bi bi-chevron-left" aria-hidden="true"></i></button>';
    var from=Math.max(1,page-2),to=Math.min(pages,page+2);
    for(var i=from;i<=to;i++)html+='<button type="button" data-page="'+i+'" '+(i===page?'aria-current="page"':'')+'>'+i+'</button>';
    html+='<button type="button" data-page="'+(page+1)+'" '+(page>=pages?'disabled':'')+' aria-label="Keyingi sahifa"><i class="bi bi-chevron-right" aria-hidden="true"></i></button>';
    els.pager.innerHTML=html;
    els.pager.querySelectorAll('[data-page]').forEach(function(button){button.addEventListener('click',function(){state.page=Number(button.dataset.page);load();document.querySelector('.oc-main').scrollIntoView({block:'start'});});});
  }
  function updateFacets(facets){facets=facets||{};var maps={specialty:new Map((facets.specialties||[]).map(function(x){return [x.code,Number(x.count||0)];})),region:new Map((facets.regions||[]).map(function(x){return [x.name,Number(x.count||0)];})),degree:new Map((facets.degrees||[]).map(function(x){return [String(x.name||'').toLowerCase(),Number(x.count||0)];}))};els.sidebar.querySelectorAll('[data-facet]').forEach(function(label){var count=label.dataset.facet==='claimed'?Number(facets.claimed_count||0):(maps[label.dataset.facet]||new Map()).get(label.dataset.value)||0;var output=label.querySelector('[data-count]');if(output)output.textContent=formatNumber(count);});var institutions=new Map((facets.institutions||[]).map(function(x){return [x.name,Number(x.count||0)];}));byId('oc-institution-list').querySelectorAll('option').forEach(function(option){option.textContent=formatNumber(institutions.get(option.dataset.name)||0)+' ta olim';});}
  function filterLabels(){
    var chips=[];
    if(state.q)chips.push(['q',state.q,'Qidiruv: '+state.q]);
    state.ixtisoslik.forEach(function(v){chips.push(['ixtisoslik',v,v]);});
    state.viloyat.forEach(function(v){chips.push(['viloyat',v,v]);});
    if(state.muassasa)chips.push(['muassasa',state.muassasa,state.muassasa]);
    if(state.daraja)chips.push(['daraja',state.daraja,state.daraja.toUpperCase()]);
    if(state.claimed)chips.push(['claimed','1','Tasdiqlangan profil']);
    return chips;
  }
  function renderChips(){
    var chips=filterLabels(); byId('oc-active-bar').hidden=!chips.length;
    els.chips.innerHTML=chips.map(function(c){return '<span class="oc-chip"><span>'+escapeHtml(c[2])+'</span><button type="button" data-remove="'+c[0]+'" data-value="'+escapeHtml(c[1])+'" aria-label="'+escapeHtml(c[2])+' filtrini olib tashlash"><i class="bi bi-x" aria-hidden="true"></i></button></span>';}).join('');
    els.chips.querySelectorAll('[data-remove]').forEach(function(button){button.addEventListener('click',function(){removeFilter(button.dataset.remove,button.dataset.value);});});
    byId('oc-filter-badge').textContent=chips.length?'('+chips.length+')':'';
  }
  function removeFilter(key,value){
    if(key==='q'){state.q='';els.q.value='';}
    else if(key==='ixtisoslik'||key==='viloyat'){state[key]=state[key].filter(function(v){return v!==value;});els.sidebar.querySelectorAll('input[name="'+key+'"]').forEach(function(input){if(input.value===value)input.checked=false;});}
    else{state[key]='';if(key==='muassasa')byId('oc-institution').value='';else els.sidebar.querySelectorAll('input[name="'+key+'"]').forEach(function(input){input.checked=input.value==='';});}
    state.page=1;load();
  }
  function clearAll(){
    state.q='';state.ixtisoslik=[];state.viloyat=[];state.muassasa='';state.daraja='';state.claimed='';state.page=1;
    els.q.value='';byId('oc-institution').value='';
    els.sidebar.querySelectorAll('input[type="checkbox"]').forEach(function(input){input.checked=false;});
    els.sidebar.querySelectorAll('input[name="daraja"]').forEach(function(input){input.checked=input.value==='';});
    load();
  }
  function readSidebar(){
    state.ixtisoslik=[];state.viloyat=[];
    els.sidebar.querySelectorAll('input[name="ixtisoslik"]:checked').forEach(function(input){state.ixtisoslik.push(input.value);});
    els.sidebar.querySelectorAll('input[name="viloyat"]:checked').forEach(function(input){state.viloyat.push(input.value);});
    var degree=els.sidebar.querySelector('input[name="daraja"]:checked');state.daraja=degree?degree.value:'';
    state.claimed=byId('oc-claimed').checked?'1':'';state.page=1;load();
  }
  function applyInputs(){
    els.q.value=state.q;byId('oc-institution').value=state.muassasa;els.sort.value=state.sort;
    els.sidebar.querySelectorAll('input[name="ixtisoslik"]').forEach(function(input){input.checked=state.ixtisoslik.indexOf(input.value)>=0;});
    els.sidebar.querySelectorAll('input[name="viloyat"]').forEach(function(input){input.checked=state.viloyat.indexOf(input.value)>=0;});
    els.sidebar.querySelectorAll('input[name="daraja"]').forEach(function(input){input.checked=input.value===state.daraja;});
    byId('oc-claimed').checked=Boolean(state.claimed);setView(state.view);
  }
  function setView(view){
    state.view=view==='grouped'?'grouped':'list';
    document.querySelectorAll('.oc-vtoggle button').forEach(function(button){button.setAttribute('aria-pressed',String(button.dataset.view===state.view));});
  }
  function load(){
    var sequence=++requestSequence;if(activeController)activeController.abort();activeController=new AbortController();
    els.content.setAttribute('aria-busy','true');els.content.innerHTML=loadingState();els.searchState.classList.add('loading');syncURL();
    fetch('/api/olimlar?'+urlParams(true).toString(),{signal:activeController.signal}).then(function(response){
      if(!response.ok)throw new Error('HTTP '+response.status);return response.json();
    }).then(function(data){
      if(sequence!==requestSequence||!data.ok)return;
      byId('oc-total').textContent=formatNumber(data.total);setView(data.view);state.sort=data.sort;els.sort.value=data.sort;
      renderRanking(data.total?data.top:[]);if(data.view==='grouped')renderGroups(data);else renderList(data);
      renderPager(data.page,data.pages);updateFacets(data.facets);renderChips();
    }).catch(function(error){
      if(error.name==='AbortError'||sequence!==requestSequence)return;
      els.content.innerHTML=errorState();renderRanking([]);els.pager.innerHTML='';bindStateActions();
    }).finally(function(){
      if(sequence===requestSequence){els.content.setAttribute('aria-busy','false');els.searchState.classList.remove('loading');}
    });
  }
  function closeDrawer(){
    els.sidebar.classList.remove('open');byId('oc-backdrop').classList.remove('show');byId('oc-backdrop').hidden=true;
    document.body.classList.remove('oc-drawer-open');byId('oc-mob-filter').setAttribute('aria-expanded','false');if(lastFilterButton)lastFilterButton.focus();
  }
  function openDrawer(){
    lastFilterButton=byId('oc-mob-filter');els.sidebar.classList.add('open');byId('oc-backdrop').hidden=false;byId('oc-backdrop').classList.add('show');
    document.body.classList.add('oc-drawer-open');lastFilterButton.setAttribute('aria-expanded','true');byId('oc-side-close').focus();
  }
  function init(){
    els={q:byId('oc-q'),sidebar:byId('oc-sidebar'),content:byId('oc-content'),pager:byId('oc-pager'),chips:byId('oc-chips'),sort:byId('oc-sort'),ranking:byId('oc-ranking'),rankingList:byId('oc-ranking-list'),searchState:byId('oc-search-state')};
    readURL();applyInputs();
    els.q.addEventListener('input',function(){clearTimeout(searchTimer);searchTimer=setTimeout(function(){state.q=els.q.value.trim();state.page=1;load();},400);});
    els.sidebar.addEventListener('change',function(event){if(event.target.id!=='oc-institution')readSidebar();});
    var institution=byId('oc-institution');var institutions=new Set(Array.from(byId('oc-institution-list').options).map(function(option){return option.value;}));
    institution.addEventListener('change',function(){var value=institution.value.trim();if(!value||institutions.has(value)){state.muassasa=value;state.page=1;load();}});
    institution.addEventListener('keydown',function(event){if(event.key==='Enter'){event.preventDefault();institution.blur();}});
    els.sort.addEventListener('change',function(){state.sort=els.sort.value;state.page=1;load();});
    document.querySelectorAll('.oc-vtoggle button').forEach(function(button){button.addEventListener('click',function(){setView(button.dataset.view);state.page=1;load();});});
    byId('oc-clear').addEventListener('click',clearAll);byId('oc-clear-all').addEventListener('click',clearAll);
    byId('oc-mob-filter').addEventListener('click',openDrawer);byId('oc-side-close').addEventListener('click',closeDrawer);byId('oc-drawer-apply').addEventListener('click',closeDrawer);byId('oc-backdrop').addEventListener('click',closeDrawer);
    document.addEventListener('keydown',function(event){if(event.key==='Escape'&&els.sidebar.classList.contains('open'))closeDrawer();});
    byId('oc-copy').addEventListener('click',function(){navigator.clipboard.writeText(location.href).then(function(){byId('oc-copy').setAttribute('title','Nusxalandi');});});
    var specialtySearch=byId('oc-ixt-search');specialtySearch.addEventListener('input',function(){var value=specialtySearch.value.toLocaleLowerCase('uz');byId('oc-facet-ixt').querySelectorAll('.oc-check').forEach(function(label){label.hidden=label.textContent.toLocaleLowerCase('uz').indexOf(value)<0;});});
    load();
  }
  window.ocClear=clearAll;window.__OC_TEST__={load:load,state:state};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
