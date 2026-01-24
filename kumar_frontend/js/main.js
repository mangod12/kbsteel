
(function(){
  function createGlobals(){
    if(document.getElementById('globalLoader')) return;
    const loader = document.createElement('div');
    loader.id = 'globalLoader';
    loader.className = 'position-fixed top-0 start-0 w-100 h-100 d-none justify-content-center align-items-center';
    loader.style.background = 'rgba(255,255,255,0.6)';
    loader.style.zIndex = '2000';
    loader.innerHTML = `<div class="spinner-border text-primary" role="status"><span class="visually-hidden">Loading...</span></div>`;
    document.body.appendChild(loader);

    const tc = document.createElement('div');
    tc.id = 'toastContainer';
    tc.className = 'toast-container position-fixed bottom-0 end-0 p-3';
    document.body.appendChild(tc);
  }

  function showLoader(){ const el = document.getElementById('globalLoader'); if(el){ el.classList.remove('d-none'); el.classList.add('d-flex'); } }
  function hideLoader(){ const el = document.getElementById('globalLoader'); if(el){ el.classList.add('d-none'); el.classList.remove('d-flex'); } }

  function showToast(message, type='success'){
    createGlobals();
    const container = document.getElementById('toastContainer');
    if(!container) return;
    const toastEl = document.createElement('div');
    toastEl.className = `toast align-items-center text-bg-${type} border-0`;
    toastEl.setAttribute('role','alert');
    toastEl.innerHTML = `
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    `;
    container.appendChild(toastEl);
    try{
      const toast = new bootstrap.Toast(toastEl, { delay: 3000 });
      toast.show();
      toastEl.addEventListener('hidden.bs.toast', ()=> toastEl.remove());
    }catch(e){ /* bootstrap may not be available; silently ignore */ }
  }

  // Expose
  window.showLoader = showLoader;
  window.hideLoader = hideLoader;
  window.showToast = showToast;

  // Ensure elements exist when DOM ready
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', createGlobals);
  else createGlobals();

  // Wrap fetch
  const _fetch = window.fetch.bind(window);
  window.fetch = async function(...args){
    const opts = args[1] || {};
    const method = (opts.method || 'GET').toUpperCase();
    showLoader();
    try{
      const res = await _fetch(...args);
      if(!res.ok){
        // try to read error message safely
        let msg = `Request failed (${res.status})`;
        try{ const j = await res.clone().json().catch(()=>null); if(j && j.detail) msg = j.detail; }catch(e){}
        showToast(msg, 'danger');
      }else{
        if(method !== 'GET') showToast('Operation successful', 'success');
      }
      return res;
    }catch(err){
      showToast(`Network error: ${err.message}`, 'danger');
      throw err;
    }finally{
      hideLoader();
    }
  };
})();

// Temporary role simulation (simple start) - will come from JWT/backend later
const userRole = "Boss"; // Boss | Supervisor | User

const Roles = Object.freeze({ BOSS: 'Boss', SUPERVISOR: 'Software Supervisor', USER: 'User' });
let currentRole = Roles.SUPERVISOR;

function setRole(role){
  currentRole = role;
  document.getElementById('current-role').innerText = role;
  // Show/hide boss-only elements
  document.querySelectorAll('[data-role]').forEach(el => {
    const allowed = el.getAttribute('data-role').split(',').map(r=>r.trim());
    if(allowed.includes(role)) el.style.display = '';
    else el.style.display = 'none';
  });
}

// Dummy data actions (UI only)
function addInstruction(){
  const text = document.getElementById('instruction-text').value.trim();
  if(!text) return alert('Please enter instruction text');
  const now = new Date().toLocaleString();
  const list = document.getElementById('instruction-list');
  const li = document.createElement('div');
  li.className = 'p-12 mb-10 card kb-card';
  li.innerHTML = `<div><strong>${now}</strong></div><div class="small-muted">${text}</div>`;
  list.prepend(li);
  document.getElementById('instruction-text').value='';
}

// Simulate low stock color check
function highlightLowStock(){
  document.querySelectorAll('.remaining-qty').forEach(td=>{
    const val = parseFloat(td.dataset.qty || '0');
    const total = parseFloat(td.dataset.total || '0');
    if(total>0 && val/total < 0.15){
      td.classList.add('low-stock');
    }
  });
}

// Initialize
document.addEventListener('DOMContentLoaded', ()=>{
  // Apply simple role simulation from `userRole` (per provided snippet)
  if(userRole === 'Boss') currentRole = Roles.BOSS;
  else if(userRole === 'Supervisor') currentRole = Roles.SUPERVISOR;
  else currentRole = Roles.USER;
  setRole(currentRole);
  // Attach role buttons
  document.querySelectorAll('.role-switch').forEach(btn=>{
    btn.addEventListener('click', ()=> setRole(btn.dataset.role));
  });
  // Logout handler
  document.querySelectorAll('.kb-logout').forEach(el=>{
    el.addEventListener('click', (e)=>{
      e.preventDefault();
      localStorage.removeItem('kb_token');
      localStorage.removeItem('kb_role');
      window.location.href = 'login.html';
    });
  });
  // Attach instruction submit
  const instrBtn = document.getElementById('instr-submit');
  if(instrBtn) instrBtn.addEventListener('click', addInstruction);

  // Excel upload (Phase-1 UI-only)
  const excelForm = document.getElementById('excelUploadForm');
  if(excelForm){
    excelForm.addEventListener('submit', async function(e){
      e.preventDefault();
      const fileInput = this.querySelector("input[type='file']");
      const statusEl = document.getElementById('excelStatus');
      const previewEl = document.getElementById('excelPreview');
      if(previewEl) previewEl.innerHTML = '';
      if(!fileInput || !fileInput.files || !fileInput.files.length){
        if(statusEl) statusEl.innerText = 'Please select an .xlsx file';
        return;
      }

      const file = fileInput.files[0];
      if(!file.name.toLowerCase().endsWith('.xlsx')){
        if(statusEl) statusEl.innerText = 'Only .xlsx files are supported';
        return;
      }

      const formData = new FormData();
      formData.append('file', file);
      if(statusEl) statusEl.innerHTML = 'Uploading and processing...';

      try{
        const res = await fetch('http://127.0.0.1:8001/excel/upload', {
          method: 'POST',
          body: formData,
        });

        if(!res.ok){
          const err = await res.json().catch(()=>null);
          const msg = err && err.detail ? err.detail : `Upload failed (${res.status})`;
          if(statusEl) statusEl.innerHTML = `<span class='text-danger'>${msg}</span>`;
          return;
        }

        const data = await res.json();
        if(statusEl) statusEl.innerHTML = "<span class='text-success'>Excel processed successfully</span>";
        // Render tables
        if(data && Array.isArray(data.sheets) && previewEl){
          data.sheets.forEach(sheet=>{
            const h = document.createElement('h6');
            h.textContent = sheet.sheet_name;
            h.className = 'mt-3';
            previewEl.appendChild(h);

            const table = document.createElement('table');
            // use modern table styles (no borders) — rely on CSS for spacing and shadows
            table.className = 'table table-sm';

            // thead
            const thead = document.createElement('thead');
            const tr = document.createElement('tr');
            sheet.columns.forEach(col=>{
              const th = document.createElement('th');
              th.textContent = col;
              tr.appendChild(th);
            });
            thead.appendChild(tr);
            table.appendChild(thead);

            // tbody
            const tbody = document.createElement('tbody');
            (sheet.rows || []).forEach(row=>{
              const tr = document.createElement('tr');
              (row || []).forEach(cell=>{
                const td = document.createElement('td');
                td.textContent = (cell === null || cell === undefined) ? '' : String(cell);
                tr.appendChild(td);
              });
              tbody.appendChild(tr);
            });
            table.appendChild(tbody);

            previewEl.appendChild(table);
          });
        }
      }catch(err){
        if(statusEl) statusEl.innerHTML = `<span class='text-danger'>Upload error: ${err.message}</span>`;
      }
    });
  }
  // Inventory CRUD integration
  const apiBase = 'http://127.0.0.1:8001/inventory';

  async function fetchInventory(){
    try{
      // Build query params from filter form (if present)
      const form = document.getElementById('inventoryFilterForm');
      let url = apiBase;
      if(form){
        const params = new URLSearchParams();
        const fName = document.getElementById('fName').value.trim();
        const fCode = document.getElementById('fCode').value.trim();
        const fSection = document.getElementById('fSection').value.trim();
        const fCategory = document.getElementById('fCategory').value.trim();
        const fUnit = document.getElementById('fUnit').value.trim();
        const fQtyMin = document.getElementById('fQtyMin').value.trim();
        const fQtyMax = document.getElementById('fQtyMax').value.trim();
        const fDateFrom = document.getElementById('fDateFrom').value;
        const fDateTo = document.getElementById('fDateTo').value;

        if(fName) params.append('material_name', fName);
        if(fCode) params.append('material_code', fCode);
        if(fSection) params.append('section', fSection);
        if(fCategory) params.append('category', fCategory);
        if(fUnit) params.append('unit', fUnit);
        if(fQtyMin) params.append('quantity_min', fQtyMin);
        if(fQtyMax) params.append('quantity_max', fQtyMax);
        if(fDateFrom) params.append('date_from', fDateFrom);
        if(fDateTo) params.append('date_to', fDateTo);

        const qs = params.toString();
        if(qs) url = `${apiBase}?${qs}`;
      }

      const res = await fetch(url);
      if(!res.ok) return [];
      return await res.json();
    }catch(err){
      console.error('Inventory fetch error', err);
      return [];
    }
  }

  function renderInventory(items){
    const tbody = document.querySelector('#inventoryTable tbody');
    if(!tbody) return;
    tbody.innerHTML = '';
    items.forEach(it => {
      const remaining = (Number(it.total) - Number(it.used)).toFixed(2);
      const tr = document.createElement('tr');
      tr.dataset.id = it.id;
      tr.innerHTML = `
        <td class="inv-name">${escapeHtml(it.name)}</td>
        <td class="inv-unit">${escapeHtml(it.unit)}</td>
        <td class="inv-total">${Number(it.total)}</td>
        <td class="inv-used">${Number(it.used)}</td>
        <td class="remaining-qty" data-qty="${remaining}" data-total="${it.total}">${remaining}</td>
        <td class="text-end">
          <button class="btn btn-sm btn-kb edit-btn">Edit</button>
          <button class="btn btn-sm btn-danger delete-btn">Delete</button>
        </td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.edit-btn').forEach(btn=> btn.addEventListener('click', onEdit));
    tbody.querySelectorAll('.delete-btn').forEach(btn=> btn.addEventListener('click', onDelete));
    highlightLowStock();
  }

  function escapeHtml(s){ return String(s || '').replace(/[&<>\"]/g, c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c])); }

  function onEdit(e){
    const tr = e.currentTarget.closest('tr');
    if(!tr) return;
    const id = tr.dataset.id;
    // swap cells for inputs
    const name = tr.querySelector('.inv-name').innerText;
    const unit = tr.querySelector('.inv-unit').innerText;
    const total = tr.querySelector('.inv-total').innerText;
    const used = tr.querySelector('.inv-used').innerText;
    tr.querySelector('.inv-name').innerHTML = `<input class="form-control form-control-sm edit-name" value="${name}">`;
    tr.querySelector('.inv-unit').innerHTML = `<input class="form-control form-control-sm edit-unit" value="${unit}">`;
    tr.querySelector('.inv-total').innerHTML = `<input class="form-control form-control-sm edit-total" value="${total}">`;
    tr.querySelector('.inv-used').innerHTML = `<input class="form-control form-control-sm edit-used" value="${used}">`;
    const actions = tr.querySelector('td.text-end');
    actions.innerHTML = `<button class="btn btn-sm btn-kb save-btn">Save</button> <button class="btn btn-sm btn-secondary cancel-btn">Cancel</button>`;
    actions.querySelector('.save-btn').addEventListener('click', ()=> onSave(id, tr));
    actions.querySelector('.cancel-btn').addEventListener('click', ()=> refreshInventory());
  }

  async function onSave(id, tr){
    const payload = {
      id,
      name: tr.querySelector('.edit-name').value,
      unit: tr.querySelector('.edit-unit').value,
      total: Number(tr.querySelector('.edit-total').value) || 0,
      used: Number(tr.querySelector('.edit-used').value) || 0,
    };
    try{
      const res = await fetch(`${apiBase}/${id}`, {
        method: 'PUT',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(payload),
      });
      if(!res.ok) throw new Error('Save failed');
      await refreshInventory();
    }catch(err){
      alert('Failed to save item');
      console.error(err);
    }
  }

  async function onDelete(e){
    const tr = e.currentTarget.closest('tr');
    if(!tr) return;
    const id = tr.dataset.id;
    if(!confirm('Delete this item?')) return;
    try{
      const res = await fetch(`${apiBase}/${id}`, { method: 'DELETE' });
      if(!res.ok) throw new Error('Delete failed');
      await refreshInventory();
    }catch(err){
      alert('Failed to delete item');
      console.error(err);
    }
  }

  async function addNewItem(){
    const name = document.getElementById('newName').value.trim();
    const unit = document.getElementById('newUnit').value.trim();
    const total = Number(document.getElementById('newTotal').value) || 0;
    const used = Number(document.getElementById('newUsed').value) || 0;
    if(!name) return alert('Enter material name');
    const payload = { name, unit, total, used };
    try{
      const res = await fetch(apiBase, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
      if(!res.ok) throw new Error('Add failed');
      document.getElementById('newName').value=''; document.getElementById('newUnit').value=''; document.getElementById('newTotal').value=''; document.getElementById('newUsed').value='';
      await refreshInventory();
    }catch(err){
      alert('Failed to add item');
      console.error(err);
    }
  }

  async function refreshInventory(){
    const items = await fetchInventory();
    renderInventory(items || []);
  }

  const addItemBtn = document.getElementById('addItemBtn');
  if(addItemBtn) addItemBtn.addEventListener('click', addNewItem);

  // Wire filter form buttons
  const filterSearchBtn = document.getElementById('filterSearchBtn');
  const filterResetBtn = document.getElementById('filterResetBtn');
  if(filterSearchBtn) filterSearchBtn.addEventListener('click', async ()=>{ await refreshInventory(); });
  if(filterResetBtn) filterResetBtn.addEventListener('click', async ()=>{
    const ids = ['fName','fCode','fSection','fCategory','fUnit','fQtyMin','fQtyMax','fDateFrom','fDateTo'];
    ids.forEach(id=>{ const el = document.getElementById(id); if(el) el.value = ''; });
    await refreshInventory();
  });

  // Initial load
  refreshInventory();
  // --- Tracking integration ---
  const trackApi = 'http://127.0.0.1:8001/tracking';

  async function fetchTrackingCustomers(){
    try{
      // Build query params from customers filter UI if present
      const params = new URLSearchParams();
      const s = document.getElementById('custSearch');
      const st = document.getElementById('custStatus');
      if(s && s.value.trim()) params.append('name', s.value.trim());
      if(st && st.value) params.append('status', st.value);
      const qs = params.toString();
      const url = qs ? `${trackApi}/customers?${qs}` : `${trackApi}/customers`;
      const res = await fetch(url);
      if(!res.ok) return [];
      return await res.json();
    }catch(err){ console.error('track fetch err',err); return []; }
  }

  async function renderCustomersList(){
    const rows = document.querySelector('#customersTable tbody');
    if(!rows) return;
    const items = await fetchTrackingCustomers();
    rows.innerHTML = '';
    items.forEach(c=>{
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.current_stage || 'Pending')}</td>
        <td><a class="btn btn-sm btn-kb" href="customer_details.html?id=${c.id}">View</a></td>
      `;
      rows.appendChild(tr);
    });
  }

  async function updateDashboardTracking(){
    // update summary counts and status table
    const items = await fetchTrackingCustomers();
    const fab = items.filter(i=>i.current_stage === 'Fabrication').length;
    const paint = items.filter(i=>i.current_stage === 'Painting').length;
    const disp = items.filter(i=>i.current_stage === 'Dispatch').length;
    const elF = document.getElementById('stageFabrication'); if(elF) elF.innerText = `${fab} Jobs`;
    const elP = document.getElementById('stagePainting'); if(elP) elP.innerText = `${paint} Jobs`;
    const elD = document.getElementById('stageDispatch'); if(elD) elD.innerText = `${disp} Jobs`;

    const tbl = document.querySelector('#customerStatusTable tbody');
    if(tbl){
      tbl.innerHTML = '';
      items.forEach(c=>{
        const tr = document.createElement('tr');
        const status = (c.current_stage === 'Completed') ? `<span class="badge-status status-completed">Completed</span>` : `<span class="badge-status status-inprogress">${escapeHtml(c.current_stage||'Pending')}</span>`;
        tr.innerHTML = `<td>${escapeHtml(c.name)}</td><td>${escapeHtml(c.current_stage||'Pending')}</td><td>${status}</td>`;
        tbl.appendChild(tr);
      });
    }
  }

  // Customer details page loader
  async function loadCustomerDetails(){
    const params = new URLSearchParams(window.location.search);
    const id = params.get('id');
    if(!id) return;
    try{
      const res = await fetch(`${trackApi}/customers/${id}`);
      if(!res.ok) return;
      const c = await res.json();
      document.getElementById('custName').innerText = c.name;
      document.getElementById('custProject').innerText = c.project || '';
      document.getElementById('custStage').innerText = c.current_stage || 'Pending';

      // stage tracker
      document.querySelectorAll('#stageTracker .stage').forEach(el=>{
        const s = el.dataset.stage;
        el.classList.remove('completed','in-progress','pending');
        if(c.current_stage === s){ el.classList.add('in-progress'); }
        else{
          // completed if earlier in STAGES
          const order = ['Fabrication','Painting','Dispatch'];
          const csIdx = order.indexOf(c.current_stage);
          const sIdx = order.indexOf(s);
          if(csIdx > sIdx) el.classList.add('completed');
          else el.classList.add('pending');
        }
      });

      // action buttons (Supervisor only)
      const act = document.getElementById('actionButtons');
      act.innerHTML = '';
      if(currentRole === Roles.SUPERVISOR){
        // If a stage is in-progress show complete and material options
        const cur = c.current_stage;
        if(cur && cur !== 'Completed'){
          const startBtn = document.createElement('button');
          startBtn.className = 'btn btn-kb me-2';
          startBtn.innerText = `Start ${cur}`;
          startBtn.addEventListener('click', ()=> updateStage(id, cur, 'started'));
          const compBtn = document.createElement('button');
          compBtn.className = 'btn btn-kb';
          compBtn.innerText = `Mark ${cur} Completed`;
          compBtn.addEventListener('click', ()=> updateStage(id, cur, 'completed'));
          act.appendChild(startBtn);
          act.appendChild(compBtn);
        }
      }

      // material usage visibility
      const muCard = document.getElementById('materialUsageCard');
      if(c.current_stage === 'Fabrication') muCard.style.display = '';
      else muCard.style.display = 'none';

      // usage log
      const ul = document.getElementById('usageLog'); ul.innerHTML = '';
      (c.material_usage||[]).forEach(u=>{
        const d = document.createElement('div');
        d.className = 'mb-1';
        d.innerText = `${u.ts} — ${u.name} ${u.qty} ${u.unit} by ${u.by || 'unknown'}`;
        ul.appendChild(d);
      });

      // stage history
      const sh = document.getElementById('stageHistory'); sh.innerHTML = '';
      (c.stage_history||[]).forEach(e=>{
        const d = document.createElement('div'); d.className='mb-1';
        d.innerText = `${e.ts} — ${e.stage} ${e.action} by ${e.by || 'unknown'}`;
        sh.appendChild(d);
      });

      // wire material usage add
      const muAdd = document.getElementById('muAddBtn');
      if(muAdd){
        muAdd.onclick = async ()=>{
          const name = document.getElementById('muMaterial').value.trim();
          const qty = Number(document.getElementById('muQty').value) || 0;
          if(!name || qty<=0) return alert('Enter material and qty');
          try{
            const res = await fetch(`${trackApi}/customers/${id}/material-usage`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name,unit:'kg',qty,by:currentRole}) });
            if(!res.ok) throw new Error('fail');
            loadCustomerDetails();
          }catch(err){ alert('Failed to log usage'); }
        };
      }

    }catch(err){ console.error(err); }
  }

  async function updateStage(id, stage, action){
    try{
      const res = await fetch(`${trackApi}/customers/${id}/stage`, { method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({stage,action,by:currentRole}) });
      if(!res.ok) throw new Error('fail');
      await loadCustomerDetails();
      await updateDashboardTracking();
    }catch(err){ alert('Failed to update stage'); }
  }

  // Load page-specific data
  renderCustomersList();
  updateDashboardTracking();
  loadCustomerDetails();

  // Wire customers filter buttons
  const custFilterBtn = document.getElementById('custFilterBtn');
  const custResetBtn = document.getElementById('custResetBtn');
  if(custFilterBtn) custFilterBtn.addEventListener('click', async ()=>{ await renderCustomersList(); await updateDashboardTracking(); });
  if(custResetBtn) custResetBtn.addEventListener('click', async ()=>{ const s=document.getElementById('custSearch'); const st=document.getElementById('custStatus'); if(s) s.value=''; if(st) st.value=''; await renderCustomersList(); await updateDashboardTracking(); });

  highlightLowStock();
});

  // Notifications: load and render when modal opens
  document.addEventListener('DOMContentLoaded', ()=>{
    const notifBtn = document.getElementById('kb-notifications-btn');
    const notifList = document.getElementById('kbNotificationsList');
    const notifSettingsEl = document.getElementById('kbNotificationSettings');
    const markReadBtn = document.getElementById('kbMarkReadBtn');

    async function apiFetch(path, opts={}){
      const token = localStorage.getItem('kb_token');
      opts.headers = opts.headers || {};
      if(token) opts.headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(`http://127.0.0.1:8001${path}`, opts);
      return res;
    }

    async function loadNotifications(){
      if(!notifList) return;
      notifList.innerHTML = 'Loading...';
      try{
        const res = await apiFetch('/notifications');
        if(!res.ok){ notifList.innerHTML = '<div class="text-danger">Failed to load</div>'; return; }
        const items = await res.json();
        if(!items.length) { notifList.innerHTML = '<div class="small-muted">No notifications</div>'; return; }
        const wrap = document.createElement('div');
        items.forEach(n=>{
          const id = n.id;
          const div = document.createElement('div');
          div.className = `p-2 mb-2 border ${n.read? '' : 'bg-light'}`;
          div.innerHTML = `<div class='form-check'><input class='form-check-input notif-check' type='checkbox' value='${id}' id='notif-${id}' ${n.read? '': ''}></div><div class='ms-2'><strong>[${n.level}]</strong> ${escapeHtml(n.message)} <div class='small text-muted'>${new Date(n.created_at).toLocaleString()}</div></div>`;
          wrap.appendChild(div);
        });
        notifList.innerHTML = '';
        notifList.appendChild(wrap);
      }catch(err){ notifList.innerHTML = `<div class='text-danger'>Error: ${err.message}</div>`; }
    }

    async function loadNotificationSettings(){
      if(!notifSettingsEl) return;
      notifSettingsEl.innerHTML = 'Loading...';
      try{
        const res = await apiFetch('/notifications/settings');
        if(!res.ok){ notifSettingsEl.innerHTML = '<div class="text-danger">Failed to load settings</div>'; return; }
        const s = await res.json();
        const html = `
          <div class='form-check'><input id='ns-inapp' class='form-check-input' type='checkbox' ${s.in_app? 'checked':''}><label class='form-check-label ms-2'>In-app notifications</label></div>
          <div class='form-check'><input id='ns-email' class='form-check-input' type='checkbox' ${s.email? 'checked':''}><label class='form-check-label ms-2'>Email notifications</label></div>
          <div class='form-check'><input id='ns-push' class='form-check-input' type='checkbox' ${s.push? 'checked':''}><label class='form-check-label ms-2'>Push notifications</label></div>
          <div class='mt-2'><button id='ns-save' class='btn btn-sm btn-kb'>Save settings</button></div>
        `;
        notifSettingsEl.innerHTML = html;
        document.getElementById('ns-save').addEventListener('click', async ()=>{
          const payload = { in_app: document.getElementById('ns-inapp').checked, email: document.getElementById('ns-email').checked, push: document.getElementById('ns-push').checked };
          const r = await apiFetch('/notifications/settings', { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload) });
          if(!r.ok){ showToast('Failed to save settings', 'danger'); return; }
          showToast('Notification settings saved', 'success');
          loadNotificationSettings();
        });
      }catch(err){ notifSettingsEl.innerHTML = `<div class='text-danger'>Error: ${err.message}</div>`; }
    }

    if(notifBtn){
      notifBtn.addEventListener('click', ()=>{
        // modal will show; load data
        setTimeout(()=>{ loadNotifications(); loadNotificationSettings(); }, 200);
      });
    }

    if(markReadBtn){
      markReadBtn.addEventListener('click', async ()=>{
        const checks = Array.from(document.querySelectorAll('.notif-check:checked')).map(i=>Number(i.value));
        if(!checks.length) return showToast('Select notifications to mark read', 'warning');
        const r = await apiFetch('/notifications/mark-read', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(checks) });
        if(!r.ok) return showToast('Failed to mark read', 'danger');
        const j = await r.json();
        showToast(`Marked ${j.updated} notifications`, 'success');
        loadNotifications();
      });
    }
  });
