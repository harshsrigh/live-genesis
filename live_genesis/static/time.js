/* Readable times, shared by every page. Read-only: touches nothing but <time> elements.
   A <time datetime="..." data-rel> element gets "2 hours ago" as its text and the exact local
   time as its title; without this script the page still shows the exact UTC time it was built with. */
'use strict';
(function(){
  const UNITS = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]];
  function exact(value){
    const d = new Date(value);
    if(value == null || isNaN(d)) return '';
    try { return d.toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',timeZoneName:'short'}); } catch { return ''; }
  }
  function relative(value, now){
    const d = new Date(value);
    if(value == null || isNaN(d)) return '';
    const seconds = Math.round(((now ?? Date.now()) - d.getTime())/1000);
    if(seconds < 0) return exact(value);          // a clock that disagrees: show the plain time
    if(seconds < 60) return 'just now';
    for(const [name, size] of UNITS){
      const n = Math.floor(seconds/size);
      if(n >= 1) return n+' '+name+(n === 1 ? '' : 's')+' ago';
    }
    return 'just now';
  }
  function enhance(root){
    (root || document).querySelectorAll('time[data-rel][datetime]').forEach(el=>{
      const value = el.getAttribute('datetime');
      const label = relative(value);
      if(!label) return;
      el.title = exact(value);
      el.textContent = label;
    });
  }
  window.liveGenesisTime = {exact, relative, enhance};
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ()=>enhance());
  else enhance();
})();
