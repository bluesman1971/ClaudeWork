/**
 * trips.js — Saved trips panel: load list, render, open/toggle, load individual trip.
 */

import { state } from './state.js';
import { apiFetch } from './api.js';
import { esc, showReviewScreen } from './review.js';
import { resetForm } from './form.js';
import { displayResults } from './finalize.js';

export async function loadSavedTrips() {
    try {
        const res  = await apiFetch('/trips');
        const data = await res.json();
        state._trips = data.trips || [];
        renderTripsPanel();
    } catch { /* non-fatal */ }
}

export function renderTripsPanel() {
    const inner = document.getElementById('tripsPanelInner');
    const count = document.getElementById('tripsPanelCount');
    count.textContent = state._trips.length ? `(${state._trips.length})` : '';
    if (!state._trips.length) {
        inner.innerHTML = '<div class="trips-empty">No saved trips yet. Generate a guide to save one.</div>';
        return;
    }
    inner.innerHTML = '';
    state._trips.forEach(trip => {
        const row = document.createElement('div');
        row.className = 'trip-row';
        const badge  = trip.status === 'finalized' ? 'finalized' : 'draft';
        const date   = trip.updated_at
            ? new Date(trip.updated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
            : '';
        const client      = state._clients.find(c => c.id === trip.client_id);
        const clientLabel = client ? ` · ${client.reference_code}` : '';
        row.innerHTML = `
            <div class="trip-row-info">
                <div class="trip-row-title">${esc(trip.title || trip.location)}</div>
                <div class="trip-row-meta">${date}${clientLabel}</div>
            </div>
            <span class="trip-row-badge ${badge}">${badge}</span>
            <button class="trip-row-load" onclick="loadTrip(${trip.id})" title="Load this trip">Load</button>`;
        inner.appendChild(row);
    });
}

export function toggleTripsPanel() {
    const toggle = document.getElementById('tripsPanelToggle');
    const panel  = document.getElementById('tripsPanel');
    toggle.classList.toggle('open');
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) loadSavedTrips();
}

export function openTripsPanel() {
    const toggle = document.getElementById('tripsPanelToggle');
    const panel  = document.getElementById('tripsPanel');
    toggle.classList.add('open');
    panel.classList.add('open');
    document.getElementById('tripsPanelToggle').scrollIntoView({ behavior: 'smooth' });
    loadSavedTrips();
}

export async function loadTrip(tripId) {
    try {
        const res  = await apiFetch(`/trips/${tripId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to load trip');
        const trip = data.trip;

        if (trip.status === 'finalized' && trip.final_html) {
            resetForm();
            state.rawData = {
                session_id:       trip.session_id || '',
                trip_id:          trip.id,
                location:         trip.location,
                duration:         trip.duration,
                photo_count:      trip.raw_photos.length,
                restaurant_count: trip.raw_restaurants.length,
                attraction_count: trip.raw_attractions.length,
                photos:           trip.raw_photos,
                restaurants:      trip.raw_restaurants,
                attractions:      trip.raw_attractions,
            };
            const pIdx = trip.approved_photo_indices      || trip.raw_photos.map((_, i) => i);
            const rIdx = trip.approved_restaurant_indices || trip.raw_restaurants.map((_, i) => i);
            const aIdx = trip.approved_attraction_indices || trip.raw_attractions.map((_, i) => i);
            state.approvalState.photos      = trip.raw_photos.map((_, i) => pIdx.includes(i));
            state.approvalState.restaurants = trip.raw_restaurants.map((_, i) => rIdx.includes(i));
            state.approvalState.attractions = trip.raw_attractions.map((_, i) => aIdx.includes(i));
            displayResults({
                html:             trip.final_html,
                location:         trip.location,
                duration:         trip.duration,
                photo_count:      pIdx.length,
                restaurant_count: rIdx.length,
                attraction_count: aIdx.length,
            });
        } else {
            resetForm();
            showReviewScreen({
                session_id:       trip.session_id || '',
                trip_id:          trip.id,
                location:         trip.location,
                duration:         trip.duration,
                photos:           trip.raw_photos,
                restaurants:      trip.raw_restaurants,
                attractions:      trip.raw_attractions,
                photo_count:      trip.raw_photos.length,
                restaurant_count: trip.raw_restaurants.length,
                attraction_count: trip.raw_attractions.length,
            });
        }
        document.getElementById('tripsPanelToggle').classList.remove('open');
        document.getElementById('tripsPanel').classList.remove('open');
    } catch (ex) {
        alert('Error loading trip: ' + ex.message);
    }
}
