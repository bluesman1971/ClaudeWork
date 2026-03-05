/**
 * finalize.js — Final guide generation, results display, and revise flow.
 *
 * loadSavedTrips() is imported dynamically inside generateFinalGuide() to
 * avoid a static circular dependency with trips.js.
 */

import { state } from './state.js';
import { apiFetch } from './api.js';
import { showError } from './form.js';

export async function generateFinalGuide() {
    if (!state.rawData) return;

    const approvedPhotos      = state.approvalState.photos.map((v, i) => v ? i : -1).filter(i => i >= 0);
    const approvedRestaurants = state.approvalState.restaurants.map((v, i) => v ? i : -1).filter(i => i >= 0);
    const approvedAttractions = state.approvalState.attractions.map((v, i) => v ? i : -1).filter(i => i >= 0);

    document.getElementById('reviewContainer').classList.remove('active');
    document.getElementById('finalizing').classList.add('active');
    document.getElementById('finalizing').scrollIntoView({ behavior: 'smooth' });

    try {
        const response = await apiFetch('/finalize', {
            method: 'POST',
            body: JSON.stringify({
                session_id:           state.rawData.session_id,
                trip_id:              state.rawData.trip_id || null,
                approved_photos:      approvedPhotos,
                approved_restaurants: approvedRestaurants,
                approved_attractions: approvedAttractions,
            }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Failed to generate final guide');

        document.getElementById('finalizing').classList.remove('active');
        // Refresh saved trips in background (dynamic import avoids static circular dep with trips.js)
        import('./trips.js').then(m => m.loadSavedTrips());
        displayResults(result);
    } catch (err) {
        document.getElementById('finalizing').classList.remove('active');
        document.getElementById('reviewContainer').classList.add('active');
        showError(`Error: ${err.message}`);
    }
}

export function reviseTrip() {
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('reviewContainer').classList.add('active');
    document.getElementById('reviewContainer').scrollIntoView({ behavior: 'smooth' });
}

export function displayResults(result) {
    document.getElementById('resultTitle').textContent = `${result.location} Guide`;
    const parts = [`${result.duration} days`];
    if (result.photo_count    > 0) parts.push(`${result.photo_count} photo spots`);
    if (result.restaurant_count > 0) parts.push(`${result.restaurant_count} restaurants`);
    if (result.attraction_count > 0) parts.push(`${result.attraction_count} attractions`);
    document.getElementById('resultSubtitle').textContent = parts.join(' · ');
    document.getElementById('previewFrame').srcdoc = result.html;
    window.currentLocation = result.location;
    document.getElementById('resultContainer').classList.add('active');
    document.getElementById('resultContainer').scrollIntoView({ behavior: 'smooth' });
}

export function printGuide() {
    const iframe = document.getElementById('previewFrame');
    if (iframe.contentWindow) iframe.contentWindow.print();
}
