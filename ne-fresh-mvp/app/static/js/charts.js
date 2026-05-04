
(async function(){
  const r = await fetch('/admin/stats.json');
  const data = await r.json();
  const ob = document.getElementById('ordersByDay');
  const gb = document.getElementById('gmvByDay');
  if (!ob || !gb) return;
  const labels = data.ordersByDay.map(x=>x[0]);
  const values = data.ordersByDay.map(x=>x[1]);
  new window.Chart(ob.getContext('2d'), {type:'line', data:{labels, datasets:[{label:'Orders', data:values}]}});
  const labels2 = data.gmvByDay.map(x=>x[0]);
  const values2 = data.gmvByDay.map(x=>x[1]);
  new window.Chart(gb.getContext('2d'), {type:'line', data:{labels:labels2, datasets:[{label:'GMV', data:values2}]}});
})();

(async function(){
  const res = await fetch('/admin/stats.json');
  if (!res.ok) return;
  const data = await res.json();

  // Metrics
  const active = document.getElementById('metric-active-sellers');
  const otd = document.getElementById('metric-otd');
  if (active) active.textContent = String(data.activeSellers ?? '—');
  if (otd) otd.textContent = (data.onTimePct != null ? data.onTimePct + '%' : '—');

  // Helper
  function drawLine(id, label, pairs){
    const el = document.getElementById(id);
    if (!el || !pairs || !pairs.length) return;
    const labels = pairs.map(p=>p[0]);
    const values = pairs.map(p=>p[1]);
    new window.Chart(el.getContext('2d'), {
      type:'line',
      data:{labels, datasets:[{label, data: values}]}
    });
  }
  function drawBar(id, label, pairs){
    const el = document.getElementById(id);
    if (!el || !pairs || !pairs.length) return;
    const labels = pairs.map(p=>p[0]);
    const values = pairs.map(p=>p[1]);
    new window.Chart(el.getContext('2d'), {
      type:'bar',
      data:{labels, datasets:[{label, data: values}]}
    });
  }
  function drawDoughnut(id, pairs){
    const el = document.getElementById(id);
    if (!el || !pairs || !pairs.length) return;
    const labels = pairs.map(p=>p[0]);
    const values = pairs.map(p=>p[1]);
    new window.Chart(el.getContext('2d'), {
      type:'doughnut',
      data:{labels, datasets:[{data: values}]}
    });
  }

  drawLine('ordersByDay', 'Orders', data.ordersByDay);
  drawLine('gmvByDay', 'GMV', data.gmvByDay);
  drawLine('aovByDay', 'AOV', data.aovByDay);
  drawDoughnut('categoryMix', data.categoryMix);
  drawBar('sellerPerf', 'Orders', data.sellerPerf);
})();