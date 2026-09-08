'use strict';
const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || '';
const state = {user:null, room:null, view:'home', socket:null, chat:null, generation:0, timer:null, playbackExpiry:0, mediaLoaded:false};
let endTimer;
tg?.ready(); tg?.expand();
if(tg?.isVersionAtLeast?.('7.7'))tg.disableVerticalSwipes();
 tg?.setHeaderColor?.('#111316'); tg?.setBackgroundColor?.('#111316');
let noticeTimer;
function notify(text) { $('notice').textContent=text; $('notice').hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('notice').hidden=true,5500); }
function node(tag, className, text) { const el=document.createElement(tag); if(className)el.className=className; if(text!==undefined)el.textContent=text; return el; }
function button(text, className, fn) {const el=node('button',className,text);el.type='button';el.onclick=()=>Promise.resolve().then(fn).catch(e=>notify(e.message));return el;}
function avatar(user) {const el=node('span','avatar',(user.name||'K').slice(0,1).toUpperCase());if(user.photo?.startsWith('https://')){const img=node('img');img.src=user.photo;img.referrerPolicy='no-referrer';img.alt='';img.onerror=()=>img.remove();el.replaceChildren(img);}el.title=user.name||'';return el;}
function stamp(seconds) {const d=new Date(seconds*1000),months=['yan','fev','mar','apr','may','iyn','iyl','avg','sen','okt','noy','dek'];return `${d.getDate()} ${months[d.getMonth()]} · ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
function duration(seconds) {return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;}
async function api(path,body,signal) {const r=await fetch('/api/'+path,{signal,method:body===undefined?'GET':'POST',headers:{'X-Telegram-Init-Data':initData,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const data=await r.json();if(!r.ok){const e=new Error(data.error||'Ulanish xatosi');e.status=r.status;throw e;}return data;}
async function show(view) {
  if(!state.user&&view!=='login')return;
  if(view==='admin'&&!state.user?.admin)return;
  state.view=view;document.body.classList.toggle('watching-room',view==='room');
  for(const el of document.querySelectorAll('.view'))el.hidden=el.id!=='view-'+view;
  for(const el of document.querySelectorAll('[data-view]'))el.classList.toggle('active',el.dataset.view===view);
  closeSocket();
  if(view==='home')await Promise.all([screenings(),loadCabinets()]);
  if(view==='chat')await openChat('global-chat','global');
  if(view==='admin')await dashboard();
  if(view==='profile'){profile();await loadSocial();}
  if(view==='room'&&state.room)await openChat('room-chat',state.room.id);
}
for(const el of document.querySelectorAll('[data-view]'))el.onclick=()=>show(el.dataset.view).catch(e=>notify(e.message));
$('community-link').onclick=()=>show('chat').catch(e=>notify(e.message));
$('profile-button').onclick=()=>show('profile').catch(e=>notify(e.message));
async function screenings(){
  const data=await api('screenings');$('screenings').replaceChildren();$('screening-count').textContent=`${data.screenings.length} TA SEANS`;
  if(!data.screenings.length){const empty=node('div','empty');empty.append(node('strong','','Hozircha seans yo‘q'),node('p','','Yangi kino rejalashtirilganda shu yerda ko‘rinadi. Hozir umumiy suhbatga qo‘shilishingiz mumkin.'));$('screenings').append(empty);return;}
  data.screenings.forEach((s,i)=>{
    const card=node('article','screening-card'),poster=node('div','poster'+(i%2?' alt':'')),body=node('div','card-body');
    const live=s.starts<=Date.now()/1000;
    poster.append(node('span','pill',s.vip?'✦ VIP SEANS':live?'● HOZIR EFIRDA':'NAVBATDAGI SEANS'),node('span','code',s.movie_code?`#${s.movie_code}`:'KINO'));
    body.append(node('h3','',s.title),node('p','',`${stamp(s.starts)} · ${Math.round(s.duration/60)} daqiqa`));
    if(s.description)body.append(node('p','',s.description.slice(0,150)));
    const actions=node('div','card-actions');
    if(live){actions.append(button('▶ Qo‘shilish','primary',()=>join({screening:s.id})));}
    else actions.append(node('span','pill','SEANS VAQTIDA OCHILADI'));
    const save=button(s.saved?'♥ Saqlangan':'♡ Saqlash','secondary save-movie',async()=>{save.disabled=true;try{await api('favorite',{screening:s.id,saved:!s.saved});s.saved=!s.saved;save.textContent=s.saved?'♥ Saqlangan':'♡ Saqlash';}finally{save.disabled=false;}});
    body.append(save);
    body.append(actions);card.append(poster,body);$('screenings').append(card);
  });
}
async function join(data){
 if(data.room){const admission=await api('room/request',{room:data.room});if(admission.status!=='approved'){state.pendingRoom=data.room;await show('waiting');$('waiting-status').textContent=admission.status==='rejected'?'Kabinet egasi so‘rovingizni rad etdi.':'So‘rov yuborildi. Kabinet egasi ruxsat bergach, quyidagi tugmani bosing.';return;}}
 const room=await api('join',data);state.pendingRoom=null;$('room-poll-panel').open=false;$('room-friends-panel').open=false;$('room-poll').replaceChildren();$('room-friends').replaceChildren();state.room=room;state.mediaLoaded=false;state.playbackExpiry=0;await show('room');renderRoom(room);clearInterval(state.timer);state.timer=setInterval(tickRoom,20000);await loadPlayback();}
function renderRoom(room){
  state.room=room;$('room-invite').hidden=!room.private;if(room.private&&state.botUsername)$('room-invite').value=`https://t.me/${state.botUsername}?startapp=room_${room.id}`;$('room-title').textContent=room.title;$('room-count').textContent=`${room.members.length} / 20`;
  $('members').replaceChildren(...room.members.map(avatar));
  $('owner-controls').hidden=!(room.private&&room.owner===state.user.id);
  $('room-status').textContent=room.private?`${room.movie_title||room.title} · boshqaruv kabinet egasida`:'Ommaviy seans · hamma bir vaqtda tomosha qiladi';
  $('seek').max=room.duration;$('seek').value=Math.floor(room.position);$('position-label').textContent=duration(room.position);
  clearTimeout(endTimer);if(!room.personal)endTimer=setTimeout(()=>{exitRoom().catch(e=>notify(e.message));notify('Seans yakunlandi. Suhbatni davom ettiramiz!');},Math.max(0,(room.ends-room.server_time)*1000));
  renderAccess(room);
  syncPlayer(room);
}
function syncPlayer(room){const p=$('player');if(!state.mediaLoaded)return;if(Math.abs(p.currentTime-room.position)>2.5)p.currentTime=room.position;if(room.playing){p.play().then(()=>$('start-player').hidden=true).catch(()=>$('start-player').hidden=false);}else p.pause();}
let playbackRequest=null;
async function loadPlayback(){
 const rid=state.room?.id;if(!rid)return;
 playbackRequest?.abort();const request=new AbortController();playbackRequest=request;
 const timeout=setTimeout(()=>request.abort(),30000);
 clearVideoLoading();videoLastProgress=Date.now();videoLastTime=0;videoLoading('Video yuklanmoqda…');$('retry-video').disabled=true;
 try{
  const data=await api('playback?room='+encodeURIComponent(rid),undefined,request.signal);
  if(state.room?.id!==rid||playbackRequest!==request)return;
  state.playbackExpiry=Date.now()+data.expires_in*1000;
  const p=$('player');state.mediaLoaded=false;
  p.onloadedmetadata=()=>{if(state.room?.id===rid){state.mediaLoaded=true;syncPlayer(state.room);}};
  p.src=data.url;renderRoom(data.room);
 }catch(e){
  if(state.room?.id!==rid||playbackRequest!==request)return;
  reportVideo('playback_unavailable');
  videoFailed(e.name==='AbortError'?'Ulanish uzoq kutildi. Qayta urinib ko‘ring.':e.message);
 }finally{clearTimeout(timeout);if(playbackRequest===request){playbackRequest=null;$('retry-video').disabled=false;}}
}

let ticking=false;
async function tickRoom(){if(!state.room||ticking)return;const rid=state.room.id;ticking=true;try{const room=await api('room?id='+encodeURIComponent(rid));if(state.room?.id===rid){renderRoom(room);if($('room-poll-panel').open)await refreshPoll();}}catch(e){if(state.room?.id!==rid)return;if([403,404,410].includes(e.status)){await exitRoom();notify(e.message);}else notify(e.message);}finally{ticking=false;}}
$('start-player').onclick=()=>{if(state.room?.private&&!state.room.playing){if(state.room.owner===state.user.id)control(state.room.position,true).catch(e=>notify(e.message));else notify('Xona egasi kinoni boshlashini kuting');return;}const p=$('player');p.play().then(()=>$('start-player').hidden=true).catch(()=>notify('Videoni ijro etib bo‘lmadi. Internet yoki video formatini tekshiring.'));};
$('player').onerror=()=>{
 if(!state.room)return;
 const code=$('player').error?.code;
 reportVideo(({2:'video_network',3:'video_decode',4:'video_format'})[code]||'video_unknown');
 videoFailed(code===3||code===4?'Video ochilmadi. Qayta urinib ko‘ring.':'Video yuklanmadi. Internetni tekshiring va qayta urining.');
};
async function control(position,playing){renderRoom(await api('control',{room:state.room.id,position:Math.floor(position),playing}));}
$('toggle-play').onclick=()=>control($('player').currentTime,!state.room.playing).catch(e=>notify(e.message));
$('seek').onchange=()=>control(Number($('seek').value),state.room.playing).catch(e=>notify(e.message));
async function exitRoom(){playbackRequest?.abort();playbackRequest=null;clearVideoLoading();await closeTheater();clearInterval(state.timer);clearTimeout(endTimer);state.timer=null;state.room=null;state.mediaLoaded=false;const p=$('player');p.pause();p.removeAttribute('src');p.load();try{await api('leave',{});}catch{}await show('chat');}
$('leave-room').onclick=()=>exitRoom().catch(e=>notify(e.message));
$('share-room').onclick=async()=>{try{if(!state.botUsername)throw new Error('Bot havolasi hali sozlanmagan');const url=`https://t.me/${state.botUsername}?startapp=room_${state.room.id}`;if(tg?.openTelegramLink)tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent('Birga kino ko‘ramiz: '+state.room.title)}`);else{await navigator.clipboard.writeText(url);notify('Xona havolasi nusxalandi');}}catch(e){notify(e.message);}};
function profile(){const card=$('profile-card');card.replaceChildren();const who=node('div','profile-name');who.append(avatar(state.user),node('h2','',state.user.name));card.append(who,node('p','muted',`Telegram ID: ${state.user.id}`),node('span','pill',state.user.vip_until>Date.now()/1000?`✦ VIP · ${stamp(state.user.vip_until)} gacha`:'ODDIY OBUNA'));if(state.room)card.append(button('▶ Xonaga qaytish','primary',()=>show('room')));}
function closeSocket(){state.generation++;if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}state.chat=null;}
async function openChat(target,scope){
  const shell=$(target);shell.replaceChildren();const messages=node('div','chat-messages'),compose=node('form','chat-compose'),reply=node('div','reply-label');reply.hidden=true;
  const input=node('textarea');input.placeholder='Suhbatga qo‘shiling…';input.rows=1;input.maxLength=2000;input.setAttribute('aria-label','Xabar matni');
  const controls=node('div','compose-buttons'),media=node('div');media.hidden=!state.mediaReady;const file=node('input');file.type='file';file.hidden=true;
  media.append(button('🎙','secondary',()=>record('voice')),button('◉','secondary',()=>record('round')),button('＋','secondary',()=>{file.accept='audio/*,video/*';file.click();}));
  const send=node('button','primary','Yuborish ↑');send.type='submit';controls.append(media,send);compose.append(reply,input,controls,file);shell.append(messages,compose);
  const chat={scope,messages,reply,input,replyTo:null,loading:false,history:[],nodes:new Map(),generation:state.generation};state.chat=chat;
  compose.onsubmit=async e=>{e.preventDefault();if(!input.value.trim())return;send.disabled=true;try{await api('message',{scope,text:input.value,reply_to:chat.replyTo});input.value='';chat.replyTo=null;reply.hidden=true;await refreshChat(chat);}catch(err){notify(err.message);}finally{send.disabled=false;}};
  file.onchange=async()=>{const selected=file.files[0];if(!selected)return;try{await sendMedia(selected,selected.type.startsWith('audio/')?'voice':'round',chat);}catch(e){notify(e.message);}file.value='';};
  await refreshChat(chat);await connectChat(chat);
}
async function connectChat(chat){
  if(state.chat!==chat)return;
  try{const ticket=await api('socket-ticket',{scope:chat.scope});if(state.chat!==chat)return;const ws=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws?ticket=${encodeURIComponent(ticket.ticket)}`);state.socket=ws;
    ws.onmessage=()=>{if(state.chat===chat){refreshChat(chat).catch(e=>notify(e.message));if(chat.scope!=='global')tickRoom();}};
    ws.onclose=event=>{if(state.chat!==chat)return;if(event.code===4001&&chat.scope!=='global')tickRoom();setTimeout(()=>{if(state.chat===chat)connectChat(chat);},3000+Math.random()*2000);};
  }catch(e){if(state.chat===chat){notify(e.message);setTimeout(()=>connectChat(chat),10000);}}
}
async function refreshChat(chat,older=false){
  if(chat.loading)return;chat.loading=true;
  try{const before=older&&chat.history.length?`&before=${chat.history[0].id}`:'';const data=await api(`messages?scope=${encodeURIComponent(chat.scope)}${before}`);if(state.chat!==chat)return;
    if(older)chat.history=[...data.messages,...chat.history];else{const incoming=new Map(data.messages.map(m=>[m.id,m]));chat.history=[...chat.history.filter(m=>m.id<(data.messages[0]?.id||0)),...incoming.values()];}
    chat.history=chat.history.slice(-250);const nearBottom=chat.messages.scrollHeight-chat.messages.scrollTop-chat.messages.clientHeight<100;const oldHeight=chat.messages.scrollHeight,oldTop=chat.messages.scrollTop;
    const wanted=[];if(chat.history.length>=50){chat.loadButton ||= button('Oldingi xabarlar','secondary chat-load',()=>refreshChat(chat,true));wanted.push(chat.loadButton);}
    if(!chat.history.length){chat.empty ||= node('div','empty','Birinchi bo‘lib salom bering 👋');wanted.push(chat.empty);}
    const keep=new Set();for(const m of chat.history){keep.add(m.id);const signature=JSON.stringify(m);let hit=chat.nodes.get(m.id);if(!hit||hit.signature!==signature){hit={signature,el:renderMessage(m,chat)};chat.nodes.set(m.id,hit);}wanted.push(hit.el);}
    for(const id of chat.nodes.keys())if(!keep.has(id))chat.nodes.delete(id);
    const wantedSet=new Set(wanted);for(const child of [...chat.messages.children])if(!wantedSet.has(child))child.remove();
    wanted.forEach((el,index)=>{if(chat.messages.children[index]!==el)chat.messages.insertBefore(el,chat.messages.children[index]||null);});
    if(older)chat.messages.scrollTop=oldTop+chat.messages.scrollHeight-oldHeight;else if(nearBottom||oldHeight===0)chat.messages.scrollTop=chat.messages.scrollHeight;
  }finally{chat.loading=false;}
}
function renderMessage(m,chat){
  const line=node('div','chat-message'),body=node('div','message-body'),author=node('div','message-author',m.name);author.append(node('time','',new Date(m.created*1000).toLocaleTimeString('uz-UZ',{hour:'2-digit',minute:'2-digit'})));line.append(avatar(m),body);body.append(author);
  if(m.reply_to)body.append(node('small','muted',`↳ #${m.reply_to} xabarga javob`));body.append(node('p','message-text',m.deleted?'Xabar o‘chirilgan':m.text));
  if(m.asset_id&&!m.deleted){const load=button('▶ Ovoz / videoni ochish','secondary',async()=>{load.disabled=true;try{const data=await api('media?id='+m.id),player=node(data.kind==='voice'?'audio':'video',data.kind==='voice'?'voice-audio':'round-video');player.controls=true;player.playsInline=true;player.preload='none';player.src=data.url;load.replaceWith(player);await player.play().catch(()=>{});}finally{load.disabled=false;}});body.append(load);}
  if(!m.deleted){const actions=node('div','message-actions');actions.append(button('Javob','',()=>{chat.replyTo=m.id;chat.reply.replaceChildren(node('span','',`${m.name} ga javob`),button('×','',()=>{chat.reply.hidden=true;chat.replyTo=null;}));chat.reply.hidden=false;chat.input.focus();}),button('Shikoyat','',async()=>{await api('report',{scope:chat.scope,message_id:m.id,reason:'Foydalanuvchi shikoyati'});notify('Shikoyat adminga yuborildi');}));if(state.user.admin)actions.append(button('O‘chirish','',async()=>{await api('moderate',{message_id:m.id});await refreshChat(chat);}));body.append(actions);}
  return line;
}
async function upload(file,kind,progress=()=>{}){
  const limits={movie:8*1024**3,voice:8*1024**2,round:20*1024**2};if(!file.size||file.size>limits[kind])throw new Error('Fayl hajmi limitdan katta');
  const started=await api('upload/start',{kind,mime:file.type,size:file.size});
  for(let n=1;n<=started.parts;n++){
    const chunk=file.slice((n-1)*started.part_size,n*started.part_size);let success=false;
    for(let attempt=0;attempt<3;attempt++){const {url}=await api('upload/part',{id:started.id,number:n});try{const r=await fetch(url,{method:'PUT',body:chunk});if(!r.ok)throw new Error('Media yuklash xatosi');success=true;break;}catch(e){if(attempt===2)throw e;await new Promise(resolve=>setTimeout(resolve,1000*(attempt+1)));}}
    if(!success)throw new Error('Yuklash to‘xtadi');progress(Math.round(n/started.parts*100));
  }
  return await api('upload/complete',{id:started.id});
}
async function sendMedia(file,kind,chat){if(!chat)throw new Error('Avval chatni oching');notify('Media yuklanmoqda…');const asset=await upload(file,kind);await api('message',{scope:chat.scope,asset_id:asset.id,reply_to:chat.replyTo});chat.replyTo=null;chat.reply.hidden=true;notify('Xabar yuborildi');await refreshChat(chat);}
async function record(kind){
  const chat=state.chat;if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('Bu qurilmada yozib olish ishlamaydi. ＋ orqali tayyor fayl yuboring.');
  let stream;try{stream=await navigator.mediaDevices.getUserMedia({audio:true,video:kind==='round'?{width:{ideal:480},height:{ideal:480},facingMode:'user'}:false});}catch{throw new Error('Mikrofon/kameraga ruxsat bering yoki ＋ orqali fayl tanlang.');}
  const types=kind==='round'?['video/mp4','video/webm;codecs=vp8,opus','video/webm']:['audio/mp4','audio/webm;codecs=opus','audio/ogg;codecs=opus'];
  const mime=types.find(t=>MediaRecorder.isTypeSupported(t));if(!mime){stream.getTracks().forEach(t=>t.stop());throw new Error('Yozish formati qo‘llab-quvvatlanmadi');}
  let recorder;try{recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:900000,audioBitsPerSecond:64000});}catch(e){stream.getTracks().forEach(t=>t.stop());throw e;}
  const chunks=[];let cancel=false,bytes=0;const dialog=$('record-dialog');$('record-title').textContent=kind==='round'?'Dumaloq video':'Ovozli xabar';$('record-preview').hidden=kind!=='round';$('record-preview').srcObject=stream;$('record-timer').textContent='0:00 / 1:00';dialog.showModal();
  const start=Date.now();const timer=setInterval(()=>{$('record-timer').textContent=duration((Date.now()-start)/1000)+' / 1:00';if(Date.now()-start>=60000&&recorder.state==='recording')recorder.stop();},250);
  recorder.ondataavailable=e=>{if(e.data.size){chunks.push(e.data);bytes+=e.data.size;if(bytes>(kind==='round'?20:8)*1024**2&&recorder.state==='recording'){cancel=true;recorder.stop();notify('Yozuv hajmi limitga yetdi');}}};
  recorder.onstop=async()=>{clearInterval(timer);stream.getTracks().forEach(t=>t.stop());$('record-preview').srcObject=null;dialog.close();if(!cancel){try{await sendMedia(new Blob(chunks,{type:recorder.mimeType}),kind,chat);}catch(e){notify(e.message);}}};
  recorder.onerror=()=>{cancel=true;if(recorder.state!=='inactive')recorder.stop();clearInterval(timer);stream.getTracks().forEach(t=>t.stop());dialog.close();notify('Yozib olishda xato yuz berdi');};
  $('record-stop').onclick=()=>{if(recorder.state==='recording')recorder.stop();};$('record-cancel').onclick=()=>{cancel=true;if(recorder.state==='recording')recorder.stop();};dialog.oncancel=e=>{e.preventDefault();$('record-cancel').click();};
  recorder.start(1000);
}
async function dashboard(){const data=await api('admin');$('online-count').textContent=data.online;$('admin-seance-count').textContent=data.screenings.length;renderAdminOverview(data);$('admin-screenings').replaceChildren();for(const s of data.screenings){const item=node('div','admin-item'),detail=node('div','',s.title);detail.append(node('small','',`${stamp(s.starts)} · ${s.viewers||0} ishtirokchi · ${s.cancelled?'Bekor qilingan':s.ends<Date.now()/1000?'Tugagan':s.vip?'VIP':'Ommaviy'}`));item.append(detail);if(!s.cancelled&&s.ends>Date.now()/1000)item.append(button('Yakunlash','secondary',async()=>{if(!confirm('Seans barcha xonalarda yakunlansinmi?'))return;await api('cancel',{id:s.id});await dashboard();}));$('admin-screenings').append(item);}if(!data.screenings.length)$('admin-screenings').append(node('div','empty','Hali seans yaratilmagan.'));$('reports').replaceChildren();for(const r of data.reports){const item=node('div','panel');item.append(node('strong','',`${r.name} · ID ${r.author_id}`),node('p','message-text',r.text),node('p','muted',r.reason),button('Xabarni o‘chirish','secondary',async()=>{await api('moderate',{message_id:r.message_id});await dashboard();}));$('reports').append(item);}if(!data.reports.length)$('reports').append(node('p','muted','Hozircha shikoyat yo‘q.'));}
$('catalog-search').onclick=async()=>{try{const data=await api('catalog?q='+encodeURIComponent($('catalog-query').value));$('catalog-results').replaceChildren();for(const m of data.movies)$('catalog-results').append(button(`#${m.code} · ${m.title}${m.vip?' · VIP':''}`,'secondary',()=>{const f=$('screening-form');f.elements.title.value=m.title;f.elements.description.value=m.description;f.elements.movie_code.value=m.code;f.elements.vip.checked=m.vip;$('catalog-results').replaceChildren();$('movie-file').value='';notify('Kino tanlandi. Endi Kinoni qo‘shish tugmasini bosing.');}));if(!data.movies.length)notify('Kino topilmadi');}catch(e){notify(e.message);}};
let cancelMovieMetadata = () => {};
let suggestedMovieTitle = '';
$('movie-file').onchange=()=>{
  cancelMovieMetadata();
  const file=$('movie-file').files[0];if(!file)return;
  const form=$('screening-form');
  if(!form.elements.title.value.trim() || form.elements.title.value===suggestedMovieTitle){
    suggestedMovieTitle=file.name.replace(/\.[^.]+$/,'').replace(/_+/g,' ').slice(0,160);
    form.elements.title.value=suggestedMovieTitle;
  }
  const video=document.createElement('video'),url=URL.createObjectURL(file);
  let timer;
  const cleanup=()=>{clearTimeout(timer);video.onloadedmetadata=null;video.onerror=null;video.removeAttribute('src');video.load();URL.revokeObjectURL(url);};
  cancelMovieMetadata=cleanup;
  video.preload='metadata';
  video.onloadedmetadata=()=>{
    if($('movie-file').files[0]===file && Number.isFinite(video.duration) && video.duration>0)
      form.elements.minutes.value=(video.duration/60).toFixed(2);
    cleanup();
  };
  video.onerror=()=>{cleanup();notify('Davomiylik olinmadi. Qo‘shimcha sozlamalarda daqiqani kiriting.');};
  timer=setTimeout(()=>{cleanup();notify('Davomiylikni qo‘shimcha sozlamalarda tekshiring.');},15000);
  video.src=url;
};
$('screening-form').elements.minutes.step='0.01';
$('screening-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,submit=$('publish-button');submit.disabled=true;$('upload-progress').hidden=false;try{const file=$('movie-file').files[0];if(!file){if(!form.elements.movie_code.value)throw new Error('Kino kodini topib, natijadan kinoni tanlang');$('upload-status').textContent='Telegramdagi kino tayyorlanmoqda…';await api('screening/telegram',{code:form.elements.movie_code.value,starts:form.elements.starts.value?Math.floor(new Date(form.elements.starts.value).getTime()/1000):null,vip:form.elements.vip.checked});form.reset();$('upload-status').textContent='Kino qo‘shildi. Seanslar bo‘limidan oching.';await dashboard();return;}const asset=await upload(file,'movie',percent=>{$('upload-progress').value=percent;$('upload-status').textContent=`Yuklanmoqda: ${percent}%`;});await api('screening',{title:form.elements.title.value,description:form.elements.description.value,movie_code:form.elements.movie_code.value||null,starts:form.elements.starts.value?Math.floor(new Date(form.elements.starts.value).getTime()/1000):Math.floor(Date.now()/1000)+5,duration:Math.round(Number(form.elements.minutes.value)*60),vip:form.elements.vip.checked,asset_id:asset.id});form.reset();$('upload-status').textContent='Seans rejalashtirildi';notify('Seans yaratildi');await dashboard();}catch(err){$('upload-status').textContent=err.message;notify(err.message);}finally{submit.disabled=false;}};
$('restriction-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;try{await api('moderate',{user_id:form.elements.user_id.value,hours:form.elements.hours.value,banned:form.elements.banned.checked});notify('Cheklov yangilandi');}catch(err){notify(err.message);}};
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.room)tickRoom();});
async function boot(){if(!initData){await show('login');return;}try{const info=await api('me');state.user=info.user;state.mediaReady=info.media_ready;$('media-profile-help').hidden=!info.media_ready;state.botUsername=info.bot_username;$('file-upload-options').hidden=!info.media_ready;if(!info.media_ready){$('movie-file').value='';$('movie-file').disabled=true;}$('profile-button').replaceChildren(...avatar(state.user).childNodes);$('admin-nav').hidden=!state.user.admin;const param=tg?.initDataUnsafe?.start_param||new URLSearchParams(location.search).get('tgWebAppStartParam')||'';if(param.startsWith('room_'))await join({room:param.slice(5)});else await show('home');}catch(e){notify(e.message);if(!state.user)await show('login');else await show('home');}}
boot();

$('create-room-home').onclick=()=>show('create-cabinet').catch(e=>notify(e.message));

$('room-invite').onclick=async()=>{try{await navigator.clipboard.writeText($('room-invite').value);notify('Kabinet havolasi nusxalandi');}catch{$('room-invite').select();}};
let telegramTheater=false;
async function closeTheater(){
 if(telegramTheater){tg?.exitFullscreen?.();telegramTheater=false;}
 document.body.classList.remove('theater');$('exit-theater').hidden=true;
 if(document.fullscreenElement)await document.exitFullscreen().catch(()=>{});
}
$('exit-theater').onclick=()=>closeTheater();
$('fullscreen-player').onclick=async()=>{
 document.body.classList.add('theater');$('exit-theater').hidden=false;
 const wrap=$('player-wrap');
 try{if(wrap.requestFullscreen)await wrap.requestFullscreen();else if(tg?.isVersionAtLeast?.('8.0')){telegramTheater=!tg.isFullscreen;tg.requestFullscreen();}}catch{}
};
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement){document.body.classList.remove('theater');$('exit-theater').hidden=true;}});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeTheater();});
let pullStart=null,pullDistance=0,refreshBusy=false;
document.addEventListener('touchstart',e=>{
 pullStart=null;pullDistance=0;
 if(e.touches.length!==1||window.scrollY>0||refreshBusy||document.body.classList.contains('theater'))return;
 if(e.target.closest('input,textarea,button,video,.chat-shell,dialog'))return;
 pullStart={x:e.touches[0].clientX,y:e.touches[0].clientY};
},{passive:true});
document.addEventListener('touchmove',e=>{
 if(!pullStart)return;
 const dx=Math.abs(e.touches[0].clientX-pullStart.x),dy=e.touches[0].clientY-pullStart.y;
 if(dx>30||dy<0){pullStart=null;$('refresh-hint').hidden=true;return;}
 pullDistance=dy;if(dy>12){e.preventDefault();$('refresh-hint').hidden=false;$('refresh-hint').textContent=dy>75?'Yangilash uchun qo‘yib yuboring ↓':'Yangilash uchun pastga torting ↓';}
},{passive:false});
document.addEventListener('touchend',async()=>{
 const refresh=pullStart&&pullDistance>75;pullStart=null;pullDistance=0;$('refresh-hint').hidden=true;
 if(!refresh||refreshBusy||!state.user)return;
 refreshBusy=true;
 try{if(state.room)await tickRoom();else if(state.view==='home')await screenings();else if(state.view==='admin')await dashboard();else if(state.view==='profile'){const data=await api('me');state.user=data.user;profile();await loadSocial();}else await show(state.view);notify('Yangilandi');}catch(e){notify(e.message);}finally{refreshBusy=false;}
});
document.addEventListener('touchcancel',()=>{pullStart=null;$('refresh-hint').hidden=true;});

$('admin-refresh').onclick=()=>dashboard().catch(e=>notify(e.message));
function renderAdminOverview(data){
 $('admin-room-count').textContent=data.room_count||0;$('admin-message-count').textContent=data.messages||0;
 $('admin-updated').textContent='Yangilandi: '+new Date().toLocaleTimeString('uz-UZ',{hour:'2-digit',minute:'2-digit'});
 $('admin-rooms').replaceChildren();
 for(const room of data.rooms||[]){
  const card=node('div','admin-room-card'),head=node('div','',room.title);
  head.append(node('small','muted',`${room.private?'Shaxsiy kabinet':'Ommaviy xona'} · ${room.members}/20${room.vip?' · VIP':''}`));
  const actions=node('div','inline');actions.append(button('Batafsil','secondary',async()=>{
   const info=await api('admin/room?id='+encodeURIComponent(room.id)),box=$('admin-room-detail');box.hidden=false;box.replaceChildren(node('h3','',info.title),node('p','muted',`${info.members.length}/20 odam · ${info.messages} xabar`));
   for(const member of info.members)box.append(node('p','',`${member.name} · ID ${member.user_id}${member.user_id===info.owner?' · Xona egasi':''}`));
   if(!info.members.length)box.append(node('p','muted','Xonada hozir odam yo‘q.'));
   box.append(button('Yopish','secondary',()=>box.hidden=true));box.scrollIntoView({block:'nearest'});
  }),button('Xonaga kirish','primary',()=>join({room:room.id})));
  card.append(head,actions);$('admin-rooms').append(card);
 }
 if(!data.rooms?.length)$('admin-rooms').append(node('div','empty','Hozir faol xona yo‘q.'));
 const labels={create_screening:'Seans yaratildi',cancel_screening:'Seans yakunlandi',cancel:'Seans yakunlandi',moderate:'Moderatsiya'};
 $('admin-audit').replaceChildren();for(const item of data.audit||[]){const line=node('div','admin-item');line.append(node('span','',labels[item.action]||item.action),node('small','muted',`${stamp(item.created)} · Admin ${item.admin_id}`));$('admin-audit').append(line);}
 if(!data.audit?.length)$('admin-audit').append(node('p','muted','Hozircha amallar yo‘q.'));
}

// Club features load on demand and reuse the room's existing refresh stream.
async function loadSocial(){
 $('social-loading').textContent='Profil ma’lumotlari yuklanmoqda…';
 try{
  const data=await api('social');if(state.view!=='profile')return;
  $('friend-code').value=data.friend_code;
  for(const id of ['friend-requests','friends-list','friend-sent','my-rooms','my-invitations'])$(id).replaceChildren();
  for(const person of data.requests){
   const row=node('div','social-row');row.append(node('strong','',person.name),node('small','muted','Do‘st bo‘lishni taklif qildi'));
   const actions=node('div','inline');
   for(const [action,label] of [['accept','Qabul qilish'],['remove','Rad etish']])actions.append(button(label,'secondary',async()=>{await api('friend',{action,user_id:person.user_id});await loadSocial();}));
   row.append(actions);$('friend-requests').append(row);
  }
  for(const person of data.friends){
   const row=node('div','social-row');row.append(avatar(person),node('strong','',person.name));
   if(state.room)row.append(button('Kabinetga taklif','secondary',async()=>{await api('room/invite',{room:state.room.id,user_id:person.user_id});notify('Taklif do‘stingizning Profil bo‘limiga qo‘shildi.');}));
   row.append(button('Do‘stlikni bekor qilish','text-button',async()=>{if(!confirm('Do‘stlikni bekor qilasizmi?'))return;await api('friend',{action:'remove',user_id:person.user_id});await loadSocial();}));
   $('friends-list').append(row);
  }
  if(!data.friends.length)$('friends-list').append(node('p','muted','Do‘stingiz kodini kiriting. U so‘rovni o‘z profilida qabul qiladi.'));
  for(const person of data.sent){const row=node('div','social-row');row.append(node('span','',person.name+' · Javobi kutilmoqda'),button('Bekor qilish','text-button',async()=>{await api('friend',{action:'remove',user_id:person.user_id});await loadSocial();}));$('friend-sent').append(row);}
  for(const room of data.rooms)$('my-rooms').append(button(`${room.locked?'🔒':'＋'} ${room.title}`,'secondary library-entry',()=>join({room:room.id})));
  for(const invite of data.invitations){const row=node('div','social-row');row.append(node('strong','',invite.title),node('small','muted',invite.name+' taklif qildi'),button('Kabinetga kirish','primary',()=>join({room:invite.room})));$('my-invitations').append(row);}
  if(!data.rooms.length&&!data.invitations.length)$('my-rooms').append(node('p','muted','Hozircha kabinet yoki taklif yo‘q.'));
  renderLibrary('my-favorites',data.favorites,true);renderLibrary('my-history',data.history,false);
 }finally{$('social-loading').textContent='';}
}
function renderLibrary(id,movies,favorites){
 const box=$(id);box.replaceChildren();
 for(const movie of movies){const row=node('div','social-row'),available=!movie.cancelled&&movie.ends>Date.now()/1000;
  row.append(node('strong','',movie.title),node('small','muted',favorites?(available?'Seans mavjud':'Seans tugagan'):stamp(movie.last_seen)));
  if(available)row.append(button(movie.starts>Date.now()/1000?'Jadvalni ko‘rish':'Tomosha qilish','secondary',()=>movie.starts>Date.now()/1000?show('home'):join(movie.room?{room:movie.room}:{screening:movie.id})));
  if(favorites)row.append(button('Olib tashlash','text-button',async()=>{await api('favorite',{screening:movie.id,saved:false});await loadSocial();}));
  box.append(row);
 }
 if(!movies.length)box.append(node('p','muted',favorites?'Seans kartasidagi ♡ Saqlash tugmasini bosing.':'Hali seansga kirmagansiz.'));
}
$('social-refresh').onclick=()=>loadSocial().catch(e=>notify(e.message));
$('copy-friend-code').onclick=async()=>{try{await navigator.clipboard.writeText($('friend-code').value);notify('Do‘st kodi nusxalandi');}catch{$('friend-code').select();notify('Kodni nusxalab, do‘stingizga yuboring.');}};
$('friend-form').onsubmit=async e=>{e.preventDefault();const submit=e.currentTarget.querySelector('button');submit.disabled=true;try{await api('friend',{action:'request',code:$('friend-input').value.trim()});$('friend-input').value='';$('add-friend-panel').open=false;await loadSocial();notify('So‘rov yuborildi. Do‘stingiz uni profilidan qabul qiladi.');}catch(err){notify(err.message);}finally{submit.disabled=false;}};
$('check-admission').onclick=()=>join({room:state.pendingRoom}).catch(e=>notify(e.message));
$('cancel-admission').onclick=()=>{state.pendingRoom=null;show('home').catch(e=>notify(e.message));};
let accessSignature='';
function renderAccess(room){
 const signature=JSON.stringify([room.id,room.owner,room.locked,room.requests]);if(signature===accessSignature)return;accessSignature=signature;
 const box=$('room-access-tools');box.replaceChildren();
 if(!room.private)return;
 box.append(node('p','muted',room.locked?'🔒 Yopiq kabinet · Kirish egasining ruxsati bilan':'Havolali kabinet · Taklif havolasi orqali kiriladi'));
 if(room.owner!==state.user.id)return;
 box.append(button(room.locked?'Havola orqali kirishni ochish':'🔒 Kirishni ruxsat bilan qilish','secondary',async()=>{await api('room/access',{room:room.id,action:'lock',locked:!room.locked});await tickRoom();}));
 for(const person of room.requests||[]){const row=node('div','social-row');row.append(node('strong','',person.name+' · kirishni so‘rayapti'));
  for(const [action,label] of [['approve','Ruxsat berish'],['reject','Rad etish']])row.append(button(label,'secondary',async()=>{await api('room/access',{room:room.id,action,user_id:person.user_id});await tickRoom();}));box.append(row);
 }
}
$('room-friends-panel').ontoggle=async()=>{
 if(!$('room-friends-panel').open||!state.room)return;
 const rid=state.room.id;
 try{const data=await api('social');if(state.room?.id!==rid)return;const box=$('room-friends');box.replaceChildren();
  for(const person of data.friends)box.append(button(person.name+' · Taklif qilish','secondary library-entry',async()=>{await api('room/invite',{room:rid,user_id:person.user_id});notify('Taklif do‘stingiz profiliga qo‘shildi.');}));
  if(!data.friends.length)box.append(node('p','muted','Avval Profil bo‘limida do‘st qo‘shing.'));
 }catch(e){notify(e.message);}
};
let pollLoading=false;
async function refreshPoll(){
 if(!state.room||pollLoading)return;const rid=state.room.id;pollLoading=true;
 try{const data=await api('poll?room='+encodeURIComponent(rid));if(state.room?.id!==rid)return;const box=$('room-poll');box.replaceChildren();
  for(const choice of data.choices){const selected=data.selected===choice.id;const voteButton=button(`${selected?'✓ ':''}${choice.title}${choice.vip?' · VIP':''} — ${choice.votes} ovoz`,selected?'primary library-entry':'secondary library-entry',async()=>{voteButton.disabled=true;try{await api('vote',{room:rid,screening:choice.id});}finally{voteButton.disabled=false;}await refreshPoll();});voteButton.setAttribute('aria-pressed',String(selected));box.append(voteButton);}
  if(!data.choices.length)box.append(node('p','muted','Ovoz berish uchun boshqa rejalashtirilgan kino kerak.'));
 }finally{pollLoading=false;}
}
$('room-poll-panel').ontoggle=()=>{if($('room-poll-panel').open)refreshPoll().catch(e=>notify(e.message));};
function reportVideo(kind){if(state.room)api('diagnostic',{room:state.room.id,kind}).catch(()=>{});}
const errorLabels={video_network:'Video tarmoq xatosi',video_decode:'Video dekodlash xatosi',video_format:'Video formati ochilmadi',playback_unavailable:'Tomosha manzili olinmadi',video_unknown:'Video ochilmadi'};
async function loadDiagnostics(){
 const data=await api('diagnostics');$('diagnostics-status').replaceChildren(
  node('p','muted',`Telegram sozlamalari: ${data.telegram_configured?'kiritilgan':'yetishmaydi'}`),
  node('p','muted',`Fayl yuklash sozlamalari: ${data.uploads_configured?'kiritilgan':'ulanmagan'} · Faol video oqimlari: ${data.streams}`));
 const box=$('diagnostics-errors');box.replaceChildren(node('h3','','Oxirgi video xatolari'));
 for(const entry of data.errors){const row=node('div','social-row');row.append(node('strong','',errorLabels[entry.kind]||'Video xatosi'),node('small','muted',`${entry.title} · ${stamp(entry.created)} · ID ${entry.user_id}`));box.append(row);}
 if(!data.errors.length)box.append(node('p','muted','Hali xato qayd etilmagan. Bu barcha videolar tekshirilganini bildirmaydi.'));
}
$('diagnostics-panel').ontoggle=()=>{if($('diagnostics-panel').open)loadDiagnostics().catch(e=>notify(e.message));};
$('check-telegram').onclick=async()=>{const b=$('check-telegram');b.disabled=true;$('check-result').textContent='Tekshirilmoqda…';try{const data=await api('diagnostics/check',{});$('check-result').textContent=data.message;}catch(e){$('check-result').textContent=e.message;}finally{b.disabled=false;}};

let cabinetMovie=null;
$('cancel-create-cabinet').onclick=()=>show('home').catch(e=>notify(e.message));
$('cabinet-search').onclick=async()=>{
 const search=$('cabinet-search');search.disabled=true;$('cabinet-movies').textContent='Kinolar qidirilmoqda…';
 try{const data=await api('catalog?q='+encodeURIComponent($('cabinet-query').value));$('cabinet-movies').replaceChildren();
  for(const movie of data.movies)$('cabinet-movies').append(button(`#${movie.code} · ${movie.title}${movie.vip?' · VIP':''}`,'secondary library-entry',()=>{cabinetMovie=movie;$('cabinet-selected').textContent='Tanlandi: '+movie.title;$('cabinet-movies').replaceChildren();}));
  if(!data.movies.length)$('cabinet-movies').textContent='Kino topilmadi. Boshqa nom yoki kod bilan qidiring.';
 }catch(e){$('cabinet-movies').textContent=e.message;}finally{search.disabled=false;}
};
$('cabinet-form').onsubmit=async e=>{
 e.preventDefault();if(!cabinetMovie){notify('Avval ro‘yxatdan kinoni tanlang.');return;}
 const submit=$('cabinet-create-submit');submit.disabled=true;submit.textContent='Kabinet yaratilmoqda…';
 try{const result=await api('cabinet/create',{name:$('cabinet-name').value.trim(),code:cabinetMovie.code});$('cabinet-form').reset();cabinetMovie=null;$('cabinet-selected').textContent='Kino hali tanlanmagan.';await join({room:result.id});}
 catch(error){notify(error.message);}finally{submit.disabled=false;submit.textContent='Kabinetni yaratish';}
};
async function loadCabinets(){
 const data=await api('cabinets'),box=$('home-cabinet-list');box.replaceChildren();
 for(const room of data.rooms){const row=node('div','social-row');row.append(node('strong','',room.title),node('small','muted',room.movie_title));
  row.append(button('Kabinetga kirish','primary',()=>join({room:room.id})),button('Yopish','text-button',async()=>{if(!confirm('Kabinet barcha qatnashchilar uchun yopilsinmi?'))return;await api('cabinet/close',{room:room.id});await loadCabinets();}));box.append(row);
 }
 if(!data.rooms.length)box.append(node('p','muted','Hali kabinet yaratmagansiz. Yuqoridagi tugma orqali oching.'));
}

let videoWaitTimer=null,videoLastTime=0,videoLastProgress=Date.now();
function videoLoading(message='Internet kutilmoqda…'){
 if(!state.room)return;
 const panel=$('video-loading');
 if(!panel.hidden)return;
 panel.hidden=false;panel.classList.remove('failed');$('player').setAttribute('aria-busy','true');
 $('video-loading-text').textContent=message;$('retry-video').hidden=true;
 videoWaitTimer=setTimeout(()=>{if(!panel.hidden){$('video-loading-text').textContent='Yuklanish cho‘zildi. Internetni tekshiring.';$('retry-video').hidden=false;}},15000);
}
function clearVideoLoading(){clearTimeout(videoWaitTimer);videoWaitTimer=null;$('video-loading').hidden=true;$('player').setAttribute('aria-busy','false');}
function videoFailed(message){
 clearTimeout(videoWaitTimer);$('video-loading').hidden=false;$('video-loading').classList.add('failed');$('video-loading-text').textContent=message;$('retry-video').hidden=false;$('player').setAttribute('aria-busy','false');
}
$('retry-video').onclick=()=>{clearVideoLoading();loadPlayback().catch(e=>videoFailed(e.message));};
const watchedVideo=$('player');
for(const event of ['loadstart','waiting','seeking'])watchedVideo.addEventListener(event,()=>videoLoading(event==='loadstart'?'Video yuklanmoqda…':'Video yuklanishi kutilmoqda…'));
watchedVideo.addEventListener('stalled',()=>{if(watchedVideo.readyState<3)videoLoading();});
watchedVideo.addEventListener('playing',()=>{videoLastProgress=Date.now();clearVideoLoading();});
watchedVideo.addEventListener('canplay',()=>{videoLastProgress=Date.now();clearVideoLoading();});
watchedVideo.addEventListener('seeked',()=>{if(watchedVideo.readyState>=3)clearVideoLoading();});
watchedVideo.addEventListener('timeupdate',()=>{if(Math.abs(watchedVideo.currentTime-videoLastTime)>.05){videoLastTime=watchedVideo.currentTime;videoLastProgress=Date.now();if(!watchedVideo.seeking&&watchedVideo.readyState>=3)clearVideoLoading();}});
watchedVideo.addEventListener('ended',clearVideoLoading);
setInterval(()=>{if(state.room&&state.view==='room'&&state.room.playing&&!watchedVideo.paused&&!watchedVideo.ended&&!document.hidden&&Date.now()-videoLastProgress>6000)videoLoading();},2000);
