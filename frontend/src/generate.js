/**
 * generate.js — Form submission handler and job polling.
 *
 * Attaches the 'submit' listener to #tripForm and orchestrates the
 * generate → poll → review flow.
 */

import { state, MAX_LOCATION_LENGTH, MIN_DURATION, MAX_DURATION } from './state.js';
import { apiFetch } from './api.js';
import {
    getGroupValues, startProgressAnimation, stopProgressAnimation,
    sleep, showError,
} from './form.js';
import { showReviewScreen } from './review.js';
import { loadSavedTrips } from './trips.js';

/**
 * pollJobUntilDone — polls GET /jobs/{job_id} every 2 s until done or failed.
 * Times out after 4 minutes (120 polls).
 */
async function pollJobUntilDone(jobId) {
    const MAX_POLLS = 120;
    const msgEl = document.getElementById('loadingMessage');

    for (let i = 0; i < MAX_POLLS; i++) {
        await sleep(2000);
        let resp, job;
        try {
            resp = await apiFetch(`/jobs/${jobId}`);
            job  = await resp.json();
        } catch {
            throw new Error('Lost connection while generating. Please try again.');
        }
        if (!resp.ok) throw new Error((job && job.error) || 'Job status check failed');
        if (job.message && msgEl) msgEl.textContent = job.message;
        if (job.status === 'done')   return job.results;
        if (job.status === 'failed') throw new Error(job.error || 'Generation failed');
    }
    throw new Error('Generation timed out after 4 minutes. Please try again.');
}

// ── Attach form submit listener ───────────────────────────────────────────────

document.getElementById('tripForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    if (!state.sectionEnabled.photos && !state.sectionEnabled.dining && !state.sectionEnabled.attractions)
        return showError('Please enable at least one section (Photography, Dining, or Attractions)');

    if (state.sectionEnabled.photos) {
        const photoInterests = getGroupValues('photo_interests', 'photo_other_text');
        if (!photoInterests.length)
            return showError('Please select at least one Photography interest (or turn the section off)');
        if (document.getElementById('p5').checked && !document.getElementById('photo_other_text').value.trim())
            return showError('Please describe your other photography interest, or uncheck "Other"');
    }
    if (state.sectionEnabled.dining) {
        const cuisines = getGroupValues('cuisines', 'cuisine_other_text');
        if (!cuisines.length)
            return showError('Please select at least one Dining preference (or turn the section off)');
        if (document.getElementById('c5').checked && !document.getElementById('cuisine_other_text').value.trim())
            return showError('Please describe your other dining preference, or uncheck "Other"');
    }
    if (state.sectionEnabled.attractions) {
        const attractionVals = getGroupValues('attractions', 'attr_other_text');
        if (!attractionVals.length)
            return showError('Please select at least one Attraction category (or turn the section off)');
        if (document.getElementById('a5').checked && !document.getElementById('attr_other_text').value.trim())
            return showError('Please describe your other attraction interest, or uncheck "Other"');
    }

    const location = document.getElementById('location').value.trim();
    if (!location) return showError('Please enter a destination');
    if (location.length > MAX_LOCATION_LENGTH)
        return showError(`Destination must be ${MAX_LOCATION_LENGTH} characters or fewer`);

    const duration = parseInt(document.getElementById('duration').value, 10);
    if (isNaN(duration) || duration < MIN_DURATION || duration > MAX_DURATION)
        return showError(`Duration must be between ${MIN_DURATION} and ${MAX_DURATION} days`);

    const budget   = document.getElementById('budget').value;
    const distance = document.getElementById('distance').value;
    if (!budget || !distance)
        return showError('Please select a budget and travel radius');

    document.getElementById('submitBtn').disabled = true;
    document.getElementById('loading').classList.add('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorMessage').classList.remove('active');
    startProgressAnimation();

    const payload = {
        location, duration, budget, distance,
        accommodation: document.getElementById('accommodation').value.trim(),
        pre_planned:   document.getElementById('prePlanned').value.trim(),
        include_photos:      state.sectionEnabled.photos,
        include_dining:      state.sectionEnabled.dining,
        include_attractions: state.sectionEnabled.attractions,
        photos_per_day:      state.countConfig.photos_per_day.value,
        restaurants_per_day: state.countConfig.restaurants_per_day.value,
        attractions_per_day: state.countConfig.attractions_per_day.value,
        photo_interests: state.sectionEnabled.photos
            ? getGroupValues('photo_interests', 'photo_other_text').join(', ')
            : '',
        cuisines: state.sectionEnabled.dining
            ? getGroupValues('cuisines', 'cuisine_other_text').join(', ')
            : '',
        attractions: state.sectionEnabled.attractions
            ? getGroupValues('attractions', 'attr_other_text').join(', ')
            : '',
    };

    const clientId = document.getElementById('clientSelect').value;
    if (clientId) payload.client_id = parseInt(clientId, 10);

    const msgEl = document.getElementById('loadingMessage');
    if (msgEl) msgEl.textContent = '';

    try {
        const initResp = await apiFetch('/generate', {
            method: 'POST',
            body: JSON.stringify(payload),
        });
        const init = await initResp.json();
        if (!initResp.ok) throw new Error(init.error || 'Failed to start generation');

        const result = await pollJobUntilDone(init.job_id);
        stopProgressAnimation(true);
        loadSavedTrips();
        showReviewScreen(result);
    } catch (err) {
        stopProgressAnimation(false);
        document.getElementById('loading').classList.remove('active');
        showError(`Error: ${err.message}`);
    } finally {
        document.getElementById('submitBtn').disabled = false;
    }
});
