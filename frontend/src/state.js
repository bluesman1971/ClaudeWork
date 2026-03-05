/**
 * state.js — Single shared mutable state object.
 *
 * All modules import this object and mutate its properties directly.
 * Using an object (rather than exported `let` bindings) lets any module
 * write to state without needing setter functions.
 */

export const API_URL =
    window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
        ? `http://${window.location.hostname}:${window.location.port || 5001}`
        : '';

export const MAX_LOCATION_LENGTH = 100;
export const MIN_DURATION = 1;
export const MAX_DURATION  = 14;

export const state = {
    currentUser: null,
    _clients:    [],
    _trips:      [],
    rawData:     null,
    approvalState: { photos: [], restaurants: [], attractions: [] },

    sectionEnabled: { photos: true, dining: true, attractions: true },

    countConfig: {
        photos_per_day:      { min: 1, max: 10, value: 3 },
        restaurants_per_day: { min: 1, max: 8,  value: 3 },
        attractions_per_day: { min: 1, max: 10, value: 4 },
    },

    progressInterval: null,
};
