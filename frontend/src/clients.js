/**
 * clients.js — Client CRM: list, create.
 */

import { state } from './state.js';
import { apiFetch } from './api.js';

export async function refreshClientList() {
    try {
        const res  = await apiFetch('/clients');
        const data = await res.json();
        state._clients = data.clients || [];
        const sel = document.getElementById('clientSelect');
        const cur = sel.value;
        sel.innerHTML = '<option value="">— No client selected —</option>';
        state._clients.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = `${c.reference_code} — ${c.name}${c.company ? ' (' + c.company + ')' : ''}`;
            sel.appendChild(opt);
        });
        if (cur) sel.value = cur;
    } catch { /* non-fatal */ }
}

export function openClientModal() {
    document.getElementById('clientForm').reset();
    document.getElementById('clientModal').classList.add('active');
    setTimeout(() => document.getElementById('cName').focus(), 50);
}

export function closeClientModal() {
    document.getElementById('clientModal').classList.remove('active');
}

export async function saveNewClient(e) {
    e.preventDefault();
    const btn = document.getElementById('saveClientBtn');
    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
        const res = await apiFetch('/clients', {
            method: 'POST',
            body: JSON.stringify({
                name:                 document.getElementById('cName').value.trim(),
                email:                document.getElementById('cEmail').value.trim(),
                phone:                document.getElementById('cPhone').value.trim(),
                company:              document.getElementById('cCompany').value.trim(),
                home_city:            document.getElementById('cHomeCity').value.trim(),
                preferred_budget:     document.getElementById('cBudget').value,
                travel_style:         document.getElementById('cTravelStyle').value.trim(),
                dietary_requirements: document.getElementById('cDietary').value.trim(),
                notes:                document.getElementById('cNotes').value.trim(),
                tags:                 document.getElementById('cTags').value.trim(),
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to save client');
        closeClientModal();
        await refreshClientList();
        document.getElementById('clientSelect').value = data.client.id;
    } catch (ex) {
        alert('Error: ' + ex.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save Client';
    }
}
