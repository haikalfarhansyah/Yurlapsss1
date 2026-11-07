(() => {
  'use strict';
  const el=id=>document.getElementById(id),shell=window.yurlapsShell,api=window.YurLapsUI.api;
  let action='check',busy=false,last={state:'idle'},poll=null;
  async function update(command){
    if(shell?.update)return shell.update(command);
    if(command==='status')return (await api('/api/update')).update;
    if(command==='install'){
      const result=await api('/api/update/apply',{});
      return {...last,state:'installing',notes:result.message||''};
    }
    return (await api('/api/update/'+command,{})).update;
  }
  function render(s){
    last=s||{state:'error',error:'Status pembaruan tidak tersedia'};
    const messages={idle:'Periksa apakah versi baru tersedia.',checking:'Memeriksa rilis…',
      current:'Kamu menggunakan versi terbaru.',available:'Versi baru tersedia.',
      downloading:`Mengunduh ${last.progress||0}%`,ready:'Pembaruan siap dipasang.',
      installing:'Menyimpan data dan memasang pembaruan…',error:'Pemeriksaan atau unduhan gagal.',
      unsupported:'Update otomatis tersedia pada aplikasi Windows terpasang. Mode source menggunakan paket source terbaru.'};
    el('updateVersion').textContent=`Versi sekarang: ${last.current||shell?.version||'—'}${last.latest?' / tersedia: '+last.latest:''}`;
    el('updateStatus').textContent=last.error||messages[last.state]||messages.idle;
    if(last.state==='ready'&&last.install_supported===false)el('updateStatus').textContent='Unduhan siap. Mode source tidak dapat memasang pembaruan .exe secara otomatis.';
    el('updateNotes').textContent=last.notes||'';
    el('updateProgress').hidden=last.state!=='downloading';el('updateProgress').value=last.progress||0;
    action=last.state==='available'?'download':last.state==='ready'?'install':'check';
    el('updateAction').textContent={check:'Cek pembaruan',download:'Unduh pembaruan',install:'Pasang dan mulai ulang'}[action];
    el('updateAction').disabled=busy||['unsupported','checking','downloading','installing'].includes(last.state)||(action==='install'&&last.install_supported===false);
    el('btnUpdates').textContent=['available','ready'].includes(last.state)?'Pembaruan tersedia':'Pembaruan aplikasi';
  }
  async function status(){try{render(await update('status'));}catch(e){render({...last,state:'error',error:e.message});}}
  el('btnUpdates').onclick=()=>{el('modalUpdates').hidden=false;status();if(!poll)poll=setInterval(status,1200);};
  el('updateClose').onclick=()=>{el('modalUpdates').hidden=true;clearInterval(poll);poll=null;};
  el('updateAction').onclick=async()=>{
    if(busy)return;
    const race=window.YurLapsUI.getRace();
    if(action==='install'&&race?.status!=='idle'){el('updateStatus').textContent='Simpan atau buang race, lalu tekan Reset sebelum memasang pembaruan.';return;}
    if(action==='install'&&!confirm('Simpan latihan aktif, tutup aplikasi, lalu pasang pembaruan?'))return;
    busy=true;el('updateAction').disabled=true;
    try{render(await update(action));}catch(e){render({...last,error:e.message});}
    finally{busy=false;render(last);}
  };
  shell?.onUpdate?.(render);
})();
