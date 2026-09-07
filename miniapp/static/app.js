'use strict';
const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const initData = tg?.initData || '';
const state = {user:null, room:null, view:'home', socket:null, chat:null, generation:0, timer:null, playbackExpiry:0, mediaLoaded:false};
let endTimer;
tg?.ready(); tg?.expand(); tg?.setHeaderColor?.('#111316'); tg?.setBackgroundColor?.('#111316');
let noticeTimer;
function notify(text) { $('notice').textContent=text; $('notice').hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>$('notice').hidden=true,5500); }
function node(tag, className, text) { const el=document.createElement(tag); if(className)el.className=className; if(text!==undefined)el.textContent=text; return el; }
function button(text, className, fn) {const el=node('button',className,text);el.type='button';el.onclick=()=>Promise.resolve().then(fn).catch(e=>notify(e.message));return el;}
function avatar(user) {const el=node('span','avatar',(user.name||'K').slice(0,1).toUpperCase());if(user.photo?.startsWith('https://')){const img=node('img');img.src=user.photo;img.referrerPolicy='no-referrer';img.alt='';img.onerror=()=>img.remove();el.replaceChildren(img);}el.title=user.name||'';return el;}
function stamp(seconds) {const d=new Date(seconds*1000),months=['yan','fev','mar','apr','may','iyn','iyl','avg','sen','okt','noy','dek'];return `${d.getDate()} ${months[d.getMonth()]} · ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;}
function duration(seconds) {return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`;}
async function api(path,body) {const r=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Telegram-Init-Data':initData,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const data=await r.json();if(!r.ok){const e=new Error(data.error||'Ulanish xatosi');e.status=r.status;throw e;}return data;}
async function show(view) {
  if(!state.user&&view!=='login')return;
  if(view==='admin'&&!state.user?.admin)return;
  state.view=view;
  for(const el of document.querySelectorAll('.view'))el.hidden=el.id!=='view-'+view;
  for(const el of document.querySelectorAll('[data-view]'))el.classList.toggle('active',el.dataset.view===view);
  closeSocket();
  if(view==='home')await screenings();
  if(view==='chat')await openChat('global-chat','global');
  if(view==='admin')await dashboard();
  if(view==='profile')profile();
  if(view==='room'&&state.room)await openChat('room-chat',state.room.id);
}
for(const el of document.querySelectorAll('[data-view]'))el.onclick=()=>show(el.dataset.view).catch(e=>notify(e.message));
$('community-link').onclick=()=>show('chat').catch(e=>notify(e.message));
$('profile-button').onclick=()=>show('profile').catch(e=>notify(e.message));
async function screenings(){
  const data=await api('screenings');$('screenings').replaceChildren();$('screening-count').textContent=`${data.screenings.length} TA SEANS`;
  if(!data.screenings.length){$('screenings').append(node('div','empty','Yangi seanslar tez orada. Ungacha umumiy chatda suhbatlashamiz.'));return;}
  data.screenings.forEach((s,i)=>{
    const card=node('article','screening-card'),poster=node('div','poster'+(i%2?' alt':'')),body=node('div','card-body');
    const live=s.starts<=Date.now()/1000;
    poster.append(node('span','pill',s.vip?'✦ VIP SEANS':live?'● HOZIR EFIRDA':'NAVBATDAGI SEANS'),node('span','code',s.movie_code?`#${s.movie_code}`:'KINO'));
    body.append(node('h3','',s.title),node('p','',`${stamp(s.starts)} · ${Math.round(s.duration/60)} daqiqa`));
    if(s.description)body.append(node('p','',s.description.slice(0,150)));
    const actions=node('div','card-actions');
    if(live){actions.append(button('▶ Qo‘shilish','primary',()=>join({screening:s.id})),button('＋ Xona','secondary',()=>join({screening:s.id,private:true})));}
    else actions.append(node('span','pill','SEANS VAQTIDA OCHILADI'));
    body.append(actions);card.append(poster,body);$('screenings').append(card);
  });
}
async function join(data){const room=await api('join',data);state.room=room;state.mediaLoaded=false;state.playbackExpiry=0;await show('room');renderRoom(room);await loadPlayback();clearInterval(state.timer);state.timer=setInterval(tickRoom,20000);}
function renderRoom(room){
  state.room=room;$('room-title').textContent=room.title;$('room-count').textContent=`${room.members.length} / 20`;
  $('members').replaceChildren(...room.members.map(avatar));
  $('owner-controls').hidden=!(room.private&&room.owner===state.user.id);
  $('room-status').textContent=room.private?'Shaxsiy xona · boshqaruv xona egasida':'Ommaviy seans · hamma bir vaqtda tomosha qiladi';
  $('seek').max=room.duration;$('seek').value=Math.floor(room.position);$('position-label').textContent=duration(room.position);
  clearTimeout(endTimer);endTimer=setTimeout(()=>{exitRoom().catch(e=>notify(e.message));notify('Seans yakunlandi. Suhbatni davom ettiramiz!');},Math.max(0,(room.ends-room.server_time)*1000));
  syncPlayer(room);
}
function syncPlayer(room){const p=$('player');if(!state.mediaLoaded)return;if(Math.abs(p.currentTime-room.position)>2.5)p.currentTime=room.position;if(room.playing){p.play().then(()=>$('start-player').hidden=true).catch(()=>$('start-player').hidden=false);}else p.pause();}
async function loadPlayback(){const rid=state.room?.id;if(!rid)return;const data=await api('playback?room='+encodeURIComponent(rid));if(state.room?.id!==rid)return;state.playbackExpiry=Date.now()+data.expires_in*1000;const p=$('player');state.mediaLoaded=false;p.onloadedmetadata=()=>{if(state.room?.id===rid){state.mediaLoaded=true;syncPlayer(state.room);}};p.src=data.url;renderRoom(data.room);}
let ticking=false;
async function tickRoom(){if(!state.room||ticking)return;const rid=state.room.id;ticking=true;try{const room=await api('room?id='+encodeURIComponent(rid));if(state.room?.id===rid)renderRoom(room);}catch(e){if(state.room?.id!==rid)return;if([403,404,410].includes(e.status)){await exitRoom();notify(e.message);}else notify(e.message);}finally{ticking=false;}}
$('start-player').onclick=()=>{if(state.room?.private&&!state.room.playing){if(state.room.owner===state.user.id)control(state.room.position,true).catch(e=>notify(e.message));else notify('Xona egasi kinoni boshlashini kuting');return;}const p=$('player');p.play().then(()=>$('start-player').hidden=true).catch(()=>notify('Videoni ijro etib bo‘lmadi. Internet yoki video formatini tekshiring.'));};
$('player').onerror=()=>{if(!state.room)return;if(Date.now()>state.playbackExpiry)loadPlayback().catch(e=>notify(e.message));else notify('Video yuklanmadi. Ulanishni tekshirib, xonaga qayta kiring.');};
async function control(position,playing){renderRoom(await api('control',{room:state.room.id,position:Math.floor(position),playing}));}
$('toggle-play').onclick=()=>control($('player').currentTime,!state.room.playing).catch(e=>notify(e.message));
$('seek').onchange=()=>control(Number($('seek').value),state.room.playing).catch(e=>notify(e.message));
async function exitRoom(){clearInterval(state.timer);clearTimeout(endTimer);state.timer=null;state.room=null;state.mediaLoaded=false;const p=$('player');p.pause();p.removeAttribute('src');p.load();try{await api('leave',{});}catch{}await show('chat');}
$('leave-room').onclick=()=>exitRoom().catch(e=>notify(e.message));
$('share-room').onclick=async()=>{try{if(!state.botUsername)throw new Error('Bot havolasi hali sozlanmagan');const url=`https://t.me/${state.botUsername}?startapp=room_${state.room.id}`;if(tg?.openTelegramLink)tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent('Birga kino ko‘ramiz: '+state.room.title)}`);else{await navigator.clipboard.writeText(url);notify('Xona havolasi nusxalandi');}}catch(e){notify(e.message);}};
function profile(){const card=$('profile-card');card.replaceChildren();const who=node('div','profile-name');who.append(avatar(state.user),node('h2','',state.user.name));card.append(who,node('p','muted',`Telegram ID: ${state.user.id}`),node('span','pill',state.user.vip_until>Date.now()/1000?`✦ VIP · ${stamp(state.user.vip_until)} gacha`:'ODDIY OBUNA'));if(state.room)card.append(button('▶ Xonaga qaytish','primary',()=>show('room')));}
function closeSocket(){state.generation++;if(state.socket){state.socket.onclose=null;state.socket.close();state.socket=null;}state.chat=null;}
async function openChat(target,scope){
  const shell=$(target);shell.replaceChildren();const messages=node('div','chat-messages'),compose=node('form','chat-compose'),reply=node('div','reply-label');reply.hidden=true;
  const input=node('textarea');input.placeholder='Suhbatga qo‘shiling…';input.rows=1;input.maxLength=2000;input.setAttribute('aria-label','Xabar matni');
  const controls=node('div','compose-buttons'),media=node('div');const file=node('input');file.type='file';file.hidden=true;
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
async function dashboard(){const data=await api('admin');$('online-count').textContent=data.online;$('admin-seance-count').textContent=data.screenings.length;$('admin-screenings').replaceChildren();for(const s of data.screenings){const item=node('div','admin-item'),detail=node('div','',s.title);detail.append(node('small','',`${stamp(s.starts)} · ${s.cancelled?'Bekor qilingan':s.ends<Date.now()/1000?'Tugagan':s.vip?'VIP':'Ommaviy'}`));item.append(detail);if(!s.cancelled&&s.ends>Date.now()/1000)item.append(button('Yakunlash','secondary',async()=>{if(!confirm('Seans barcha xonalarda yakunlansinmi?'))return;await api('cancel',{id:s.id});await dashboard();}));$('admin-screenings').append(item);}if(!data.screenings.length)$('admin-screenings').append(node('div','empty','Hali seans yaratilmagan.'));$('reports').replaceChildren();for(const r of data.reports){const item=node('div','panel');item.append(node('strong','',`${r.name} · ID ${r.author_id}`),node('p','message-text',r.text),node('p','muted',r.reason),button('Xabarni o‘chirish','secondary',async()=>{await api('moderate',{message_id:r.message_id});await dashboard();}));$('reports').append(item);}if(!data.reports.length)$('reports').append(node('p','muted','Hozircha shikoyat yo‘q.'));}
$('catalog-search').onclick=async()=>{try{const data=await api('catalog?q='+encodeURIComponent($('catalog-query').value));$('catalog-results').replaceChildren();for(const m of data.movies)$('catalog-results').append(button(`#${m.code} · ${m.title}${m.vip?' · VIP':''}`,'secondary',()=>{const f=$('screening-form');f.elements.title.value=m.title;f.elements.description.value=m.description;f.elements.movie_code.value=m.code;f.elements.vip.checked=m.vip;$('catalog-results').replaceChildren();notify('Kino ma’lumotlari olindi. Video faylini ham tanlang.');}));if(!data.movies.length)notify('Kino topilmadi');}catch(e){notify(e.message);}};
$('movie-file').onchange=()=>{const f=$('movie-file').files[0];if(!f)return;const video=document.createElement('video');const url=URL.createObjectURL(f);video.preload='metadata';video.src=url;video.onloadedmetadata=()=>{if(Number.isFinite(video.duration))$('screening-form').elements.minutes.value=(video.duration/60).toFixed(2);URL.revokeObjectURL(url);};video.onerror=()=>{URL.revokeObjectURL(url);notify('Video formatini tekshiring: MP4 H.264/AAC tavsiya etiladi');};};
$('screening-form').elements.minutes.step='0.01';
$('screening-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,submit=$('publish-button');submit.disabled=true;$('upload-progress').hidden=false;try{const file=$('movie-file').files[0];if(!file)throw new Error('Kino faylini tanlang');const asset=await upload(file,'movie',percent=>{$('upload-progress').value=percent;$('upload-status').textContent=`Yuklanmoqda: ${percent}%`;});await api('screening',{title:form.elements.title.value,description:form.elements.description.value,movie_code:form.elements.movie_code.value||null,starts:Math.floor(new Date(form.elements.starts.value).getTime()/1000),duration:Math.round(Number(form.elements.minutes.value)*60),vip:form.elements.vip.checked,asset_id:asset.id});form.reset();$('upload-status').textContent='Seans rejalashtirildi';notify('Seans yaratildi');await dashboard();}catch(err){$('upload-status').textContent=err.message;notify(err.message);}finally{submit.disabled=false;}};
$('restriction-form').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget;try{await api('moderate',{user_id:form.elements.user_id.value,hours:form.elements.hours.value,banned:form.elements.banned.checked});notify('Cheklov yangilandi');}catch(err){notify(err.message);}};
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.room)tickRoom();});
async function boot(){if(!initData){await show('login');return;}try{const info=await api('me');state.user=info.user;state.botUsername=info.bot_username;$('profile-button').replaceChildren(...avatar(state.user).childNodes);$('admin-nav').hidden=!state.user.admin;const param=tg?.initDataUnsafe?.start_param||new URLSearchParams(location.search).get('tgWebAppStartParam')||'';if(param.startsWith('room_'))await join({room:param.slice(5)});else await show('home');}catch(e){notify(e.message);if(!state.user)await show('login');else await show('home');}}
boot();
