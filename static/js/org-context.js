/**
 * Sidebar MT-style org switcher: multiple profiles in localStorage, active
 * creds mirrored to sessionStorage (mt_api_key, mt_org_id, mt_org_name).
 */
(function () {
    var PROFILES_KEY = "dl_mt_profiles";
    var ACTIVE_KEY = "dl_mt_active_profile_id";
    var SHOW_ALL_KEY = "dl_show_all_orgs";

    function parseProfiles(raw) {
        try {
            var a = JSON.parse(raw);
            return Array.isArray(a) ? a : [];
        } catch (e) {
            return [];
        }
    }

    function getProfiles() {
        return parseProfiles(localStorage.getItem(PROFILES_KEY));
    }

    function setProfiles(arr) {
        localStorage.setItem(PROFILES_KEY, JSON.stringify(arr));
    }

    function migrateFromSession() {
        if (localStorage.getItem(PROFILES_KEY)) return;
        var k = sessionStorage.getItem("mt_api_key");
        var o = sessionStorage.getItem("mt_org_id");
        if (!k || !o) return;
        var n = sessionStorage.getItem("mt_org_name") || "Default";
        var id = "m_" + Date.now();
        setProfiles([{ id: id, label: n, orgId: o, apiKey: k }]);
        localStorage.setItem(ACTIVE_KEY, id);
    }

    function applyToSession(profile) {
        if (!profile) {
            sessionStorage.removeItem("mt_api_key");
            sessionStorage.removeItem("mt_org_id");
            sessionStorage.removeItem("mt_org_name");
            return;
        }
        sessionStorage.setItem("mt_api_key", profile.apiKey);
        sessionStorage.setItem("mt_org_id", profile.orgId);
        sessionStorage.setItem("mt_org_name", profile.label || profile.orgId);
    }

    function getActiveProfile() {
        var profiles = getProfiles();
        if (!profiles.length) return null;
        var aid = localStorage.getItem(ACTIVE_KEY);
        if (aid) {
            for (var i = 0; i < profiles.length; i++) {
                if (profiles[i].id === aid) return profiles[i];
            }
        }
        return profiles[0];
    }

    function isShowAllOrgs() {
        return sessionStorage.getItem(SHOW_ALL_KEY) === "1";
    }

    function setShowAllOrgs(on) {
        if (on) sessionStorage.setItem(SHOW_ALL_KEY, "1");
        else sessionStorage.removeItem(SHOW_ALL_KEY);
        syncShowAllCheckboxes();
        document.dispatchEvent(new CustomEvent("dl-org-context-changed"));
    }

    function syncShowAllCheckboxes() {
        var v = isShowAllOrgs();
        document.querySelectorAll(".dl-show-all-orgs-input").forEach(function (el) {
            el.checked = v;
        });
    }

    function shortenOrgId(s) {
        if (!s || s.length <= 20) return s || "";
        return s.slice(0, 10) + "…" + s.slice(-6);
    }

    function closePanel() {
        var p = document.querySelector(".org-switcher-panel");
        var t = document.querySelector(".org-switcher-trigger");
        if (p) p.classList.add("hidden");
        if (t) t.setAttribute("aria-expanded", "false");
    }

    function openPanel() {
        var p = document.querySelector(".org-switcher-panel");
        var t = document.querySelector(".org-switcher-trigger");
        if (p) p.classList.remove("hidden");
        if (t) t.setAttribute("aria-expanded", "true");
    }

    function renderSwitcher() {
        var root = document.getElementById("sidebar-org-switcher");
        if (!root) return;

        var profiles = getProfiles();
        var active = getActiveProfile();
        applyToSession(active);

        var name = active ? active.label : "Add organization";
        var sub = active ? shortenOrgId(active.orgId) : "Required for API calls";

        var listHtml = profiles
            .map(function (p) {
                var sel = active && p.id === active.id ? " org-switcher-item--active" : "";
                return (
                    '<li class="org-switcher-item' +
                    sel +
                    '" data-profile-id="' +
                    escapeAttr(p.id) +
                    '">' +
                    '<button type="button" class="org-switcher-item-btn">' +
                    '<span class="org-switcher-item-name">' +
                    escapeHtml(p.label || p.orgId) +
                    "</span>" +
                    '<span class="org-switcher-item-id">' +
                    escapeHtml(shortenOrgId(p.orgId)) +
                    "</span>" +
                    "</button>" +
                    '<button type="button" class="org-switcher-remove" data-remove-id="' +
                    escapeAttr(p.id) +
                    '" title="Remove">&times;</button></li>'
                );
            })
            .join("");

        root.innerHTML =
            '<div class="org-switcher">' +
            '<button type="button" class="org-switcher-trigger" aria-expanded="false" aria-haspopup="listbox">' +
            '<div class="org-switcher-labels">' +
            '<span class="org-switcher-name">' +
            escapeHtml(name) +
            "</span>" +
            '<span class="org-switcher-sub">' +
            escapeHtml(sub) +
            "</span>" +
            "</div>" +
            '<svg class="org-switcher-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>' +
            "</button>" +
            '<div class="org-switcher-panel hidden" role="listbox">' +
            '<ul class="org-switcher-list">' +
            listHtml +
            "</ul>" +
            '<div class="org-switcher-form hidden" id="org-switcher-add-form">' +
            '<label class="org-switcher-field"><span>Display name</span>' +
            '<input type="text" class="mt-input org-switcher-input" id="dl-new-org-label" placeholder="Sandbox" autocomplete="off"></label>' +
            '<label class="org-switcher-field"><span>Organization ID</span>' +
            '<input type="text" class="mt-input org-switcher-input" id="dl-new-org-id" placeholder="org_..." autocomplete="off"></label>' +
            '<label class="org-switcher-field"><span>API key</span>' +
            '<input type="password" class="mt-input org-switcher-input" id="dl-new-api-key" placeholder="sk_test_..." autocomplete="off"></label>' +
            '<div class="org-switcher-form-actions">' +
            '<button type="button" class="btn btn-primary btn-sm" id="dl-save-org-profile">Save</button>' +
            '<button type="button" class="btn btn-outline btn-sm" id="dl-cancel-org-profile">Cancel</button>' +
            "</div>" +
            '<p class="org-switcher-security-note">Stored in this browser only (localStorage). Not encrypted — use a dedicated sandbox key.</p>' +
            "</div>" +
            '<div class="org-switcher-footer">' +
            '<button type="button" class="btn btn-ghost btn-sm org-switcher-add-btn" id="dl-open-add-org">+ Add organization</button>' +
            '<label class="org-switcher-all-label">' +
            '<input type="checkbox" class="dl-show-all-orgs-input" id="dl-show-all-orgs-sidebar">' +
            " Show all orgs</label>" +
            "</div>" +
            "</div>" +
            "</div>";

        var trigger = root.querySelector(".org-switcher-trigger");
        var panel = root.querySelector(".org-switcher-panel");
        var addForm = root.querySelector("#org-switcher-add-form");
        var showAll = root.querySelector("#dl-show-all-orgs-sidebar");

        if (showAll) {
            showAll.checked = isShowAllOrgs();
            showAll.addEventListener("change", function () {
                setShowAllOrgs(showAll.checked);
            });
        }

        trigger.addEventListener("click", function (e) {
            e.stopPropagation();
            if (panel.classList.contains("hidden")) openPanel();
            else closePanel();
        });

        root.querySelectorAll(".org-switcher-item-btn").forEach(function (btn) {
            btn.addEventListener("click", function () {
                var li = btn.closest(".org-switcher-item");
                var pid = li && li.getAttribute("data-profile-id");
                if (pid) {
                    localStorage.setItem(ACTIVE_KEY, pid);
                    applyToSession(getActiveProfile());
                    document.dispatchEvent(new CustomEvent("dl-org-context-changed"));
                    renderSwitcher();
                    closePanel();
                }
            });
        });

        root.querySelectorAll(".org-switcher-remove").forEach(function (btn) {
            btn.addEventListener("click", function (e) {
                e.stopPropagation();
                var rid = btn.getAttribute("data-remove-id");
                if (!rid) return;
                var next = getProfiles().filter(function (p) {
                    return p.id !== rid;
                });
                setProfiles(next);
                if (localStorage.getItem(ACTIVE_KEY) === rid) {
                    localStorage.removeItem(ACTIVE_KEY);
                    if (next[0]) localStorage.setItem(ACTIVE_KEY, next[0].id);
                }
                applyToSession(getActiveProfile());
                document.dispatchEvent(new CustomEvent("dl-org-context-changed"));
                renderSwitcher();
            });
        });

        var openAdd = root.querySelector("#dl-open-add-org");
        if (openAdd) {
            openAdd.addEventListener("click", function (e) {
                e.stopPropagation();
                addForm.classList.toggle("hidden");
            });
        }

        root.querySelector("#dl-cancel-org-profile").addEventListener("click", function () {
            addForm.classList.add("hidden");
        });

        root.querySelector("#dl-save-org-profile").addEventListener("click", function () {
            var label = document.getElementById("dl-new-org-label").value.trim();
            var oid = document.getElementById("dl-new-org-id").value.trim();
            var key = document.getElementById("dl-new-api-key").value.trim();
            if (!oid || !key) {
                alert("Organization ID and API key are required.");
                return;
            }
            var profiles = getProfiles();
            var id = "m_" + Date.now() + "_" + Math.random().toString(36).slice(2, 6);
            profiles.push({
                id: id,
                label: label || oid,
                orgId: oid,
                apiKey: key,
            });
            setProfiles(profiles);
            localStorage.setItem(ACTIVE_KEY, id);
            applyToSession(getActiveProfile());
            document.dispatchEvent(new CustomEvent("dl-org-context-changed"));
            renderSwitcher();
        });
    }

    var _docCloseBound = false;

    function bindDocumentCloseOnce() {
        if (_docCloseBound) return;
        _docCloseBound = true;
        document.addEventListener("click", function (e) {
            var root = document.getElementById("sidebar-org-switcher");
            if (!root || root.contains(e.target)) return;
            closePanel();
        });
    }

    function escapeHtml(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function escapeAttr(s) {
        return String(s)
            .replace(/&/g, "&amp;")
            .replace(/"/g, "&quot;")
            .replace(/</g, "&lt;");
    }

    function init() {
        bindDocumentCloseOnce();
        migrateFromSession();
        var profiles = getProfiles();
        var active = getActiveProfile();
        if (active && !localStorage.getItem(ACTIVE_KEY)) {
            localStorage.setItem(ACTIVE_KEY, active.id);
        }
        applyToSession(getActiveProfile());
        renderSwitcher();
        syncShowAllCheckboxes();
    }

    window.DLOrgContext = {
        init: init,
        renderSwitcher: renderSwitcher,
        getActiveProfile: getActiveProfile,
        isShowAllOrgs: isShowAllOrgs,
        setShowAllOrgs: setShowAllOrgs,
        applyToSession: applyToSession,
    };

    document.addEventListener("DOMContentLoaded", init);
})();
