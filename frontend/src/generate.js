/**
 * generate.js — Form submission handler and job polling.
 *
 * Attaches the 'submit' listener to #tripForm and orchestrates the
 * generate → poll → review flow.
 */

import { state, MAX_LOCATION_LENGTH } from './state.js';
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

    // ── Validate location ─────────────────────────────────────────────────────
    const location = document.getElementById('location').value.trim();
    if (!location) return showError('Please enter a destination');
    if (location.length > MAX_LOCATION_LENGTH)
        return showError(`Destination must be ${MAX_LOCATION_LENGTH} characters or fewer`);

    // ── Validate dates ────────────────────────────────────────────────────────
    const startDateVal = document.getElementById('startDate').value;
    const endDateVal   = document.getElementById('endDate').value;
    if (!startDateVal || !endDateVal)
        return showError('Please enter both a start date and end date');
    const startDate = new Date(startDateVal);
    const endDate   = new Date(endDateVal);
    if (endDate < startDate)
        return showError('End date must be on or after the start date');
    const durationDays = Math.round((endDate - startDate) / (1000 * 60 * 60 * 24)) + 1;
    if (durationDays > 14)
        return showError('Trip duration cannot exceed 14 days');

    // ── Validate photo interests ──────────────────────────────────────────────
    if (state.sectionEnabled.photos) {
        const photoInterests = getGroupValues('photo_interests', 'photo_other_text');
        if (!photoInterests.length)
            return showError('Please select at least one Photography interest (or turn the section off)');
        if (document.getElementById('p5').checked && !document.getElementById('photo_other_text').value.trim())
            return showError('Please describe your other photography interest, or uncheck "Other"');
    }

    // ── Validate budget + distance ────────────────────────────────────────────
    const budget   = document.getElementById('budget').value;
    const distance = document.getElementById('distance').value;
    if (!budget || !distance)
        return showError('Please select a budget and travel radius');

    document.getElementById('submitBtn').disabled = true;
    document.getElementById('loading').classList.add('active');
    document.getElementById('resultContainer').classList.remove('active');
    document.getElementById('errorMessage').classList.remove('active');
    startProgressAnimation();

    // ── Build payload ─────────────────────────────────────────────────────────
    const payload = {
        location,
        start_date: startDateVal,
        end_date:   endDateVal,
        budget,
        distance,
        accommodation: document.getElementById('accommodation').value.trim(),
        pre_planned:   document.getElementById('prePlanned').value.trim(),
        photos_per_day: state.countConfig.photos_per_day.value,
        photo_interests: state.sectionEnabled.photos
            ? getGroupValues('photo_interests', 'photo_other_text').join(', ')
            : '',
    };

    // Gear profile
    const gearSel = document.getElementById('gearProfileSelect');
    if (gearSel && gearSel.value) payload.gear_profile_id = parseInt(gearSel.value, 10);

    // Client
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
