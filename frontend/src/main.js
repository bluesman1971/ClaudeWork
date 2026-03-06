/**
 * main.js — Application entry point.
 *
 * Responsibilities:
 *  1. Import all modules (side-effect: generate.js attaches the form submit listener)
 *  2. Listen for auth events fired by api.js and wire up client/trip/gear refresh
 *  3. Expose functions that are referenced by inline onclick handlers in the HTML
 *     (required because ES modules are scoped — not automatically global)
 *  4. Bootstrap: call checkAuth() on page load
 */

import { checkAuth, handleLogin, handleLogout } from './api.js';
import {
    refreshClientList, openClientModal, closeClientModal, saveNewClient,
    refreshGearProfiles, openGearProfileModal, closeGearProfileModal,
    saveGearProfile, deleteGearProfile, openGearPanel, closeGearPanel,
} from './clients.js';
import { loadSavedTrips, toggleTripsPanel, openTripsPanel, loadTrip } from './trips.js';
import {
    toggleItem, bulkSelect, toggleEditPanel, saveItemEdit, replaceItem,
} from './review.js';
import {
    toggleSection, adjustCount, toggleOther, resetForm,
} from './form.js';
import { generateFinalGuide, reviseTrip, printGuide } from './finalize.js';
import './generate.js';  // attaches the form submit listener (side-effect import)

// ── Auth event wiring ────────────────────────────────────────────────────────

document.addEventListener('auth:success', () => {
    refreshClientList();
    loadSavedTrips();
    refreshGearProfiles();
});

document.addEventListener('auth:logout', () => {
    resetForm();
});

// ── Window exports (required for inline onclick handlers in HTML) ─────────────
//
// ES modules are scoped by default. Functions called from onclick="..." in the
// HTML or from dynamically generated innerHTML strings must be on window.

// Auth
window.handleLogin       = handleLogin;
window.handleLogout      = handleLogout;

// Clients
window.openClientModal   = openClientModal;
window.closeClientModal  = closeClientModal;
window.saveNewClient     = saveNewClient;
window.refreshClientList = refreshClientList;

// Gear profiles
window.refreshGearProfiles    = refreshGearProfiles;
window.openGearProfileModal   = openGearProfileModal;
window.closeGearProfileModal  = closeGearProfileModal;
window.saveGearProfile        = saveGearProfile;
window.deleteGearProfile      = deleteGearProfile;
window.openGearPanel          = openGearPanel;
window.closeGearPanel         = closeGearPanel;

// Trips
window.toggleTripsPanel  = toggleTripsPanel;
window.openTripsPanel    = openTripsPanel;
window.loadTrip          = loadTrip;

// Form
window.toggleSection     = toggleSection;
window.adjustCount       = adjustCount;
window.toggleOther       = toggleOther;
window.resetForm         = resetForm;

// Review (called from dynamically generated HTML)
window.toggleItem        = toggleItem;
window.bulkSelect        = bulkSelect;
window.toggleEditPanel   = toggleEditPanel;
window.saveItemEdit      = saveItemEdit;
window.replaceItem       = replaceItem;

// Finalize
window.generateFinalGuide = generateFinalGuide;
window.reviseTrip         = reviseTrip;
window.printGuide         = printGuide;

// ── Bootstrap ────────────────────────────────────────────────────────────────

checkAuth();
