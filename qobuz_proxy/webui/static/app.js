(function () {
    "use strict";

    var pollTimer = null;

    function showAuthState(state) {
        document.getElementById("auth-disconnected").style.display = "none";
        document.getElementById("auth-login").style.display = "none";
        document.getElementById("auth-connected").style.display = "none";

        if (state === "disconnected") {
            document.getElementById("auth-disconnected").style.display = "";
        } else if (state === "login") {
            document.getElementById("auth-login").style.display = "";
        } else if (state === "connected") {
            document.getElementById("auth-connected").style.display = "";
        }
    }

    function updateSpeakers(speakers) {
        var container = document.getElementById("speakers-list");
        if (!speakers || speakers.length === 0) {
            container.innerHTML = '<p class="muted">No speakers configured.</p>';
            return;
        }

        var html = "";
        for (var i = 0; i < speakers.length; i++) {
            var s = speakers[i];
            var statusClass = s.connected ? "speaker-status-connected" : "speaker-status-disconnected";
            var statusText = s.connected ? "connected" : "disconnected";
            html += '<div class="speaker-item">';
            html += '<span class="status-dot ' + (s.connected ? "status-success" : "") + '"></span>';
            html += '<span class="speaker-name">' + escapeHtml(s.name) + "</span>";
            if (s.backend) {
                html += '<span class="speaker-backend">' + escapeHtml(s.backend) + "</span>";
            }
            html += '<span class="speaker-status ' + statusClass + '">' + statusText + "</span>";
            html += "</div>";
        }
        container.innerHTML = html;
    }

    function updateSystemInfo(system) {
        if (!system) return;
        document.getElementById("system-version").textContent = system.version || "--";
        document.getElementById("system-uptime").textContent = system.uptime || "--";
    }

    function escapeHtml(text) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    function fetchStatus() {
        fetch("/api/status")
            .then(function (response) {
                if (!response.ok) throw new Error("HTTP " + response.status);
                return response.json();
            })
            .then(function (data) {
                // Update auth state
                if (data.authenticated) {
                    document.getElementById("auth-email").textContent = data.email || "";
                    showAuthState("connected");
                } else {
                    // Only switch to disconnected if we're not in login flow
                    var loginDiv = document.getElementById("auth-login");
                    if (loginDiv.style.display === "none") {
                        showAuthState("disconnected");
                    }
                }

                // Update speakers
                if (data.authenticated && data.speakers) {
                    updateSpeakers(data.speakers);
                } else if (!data.authenticated) {
                    document.getElementById("speakers-list").innerHTML =
                        '<p class="muted">Waiting for authentication...</p>';
                }

                // Update system info
                updateSystemInfo(data.system);
            })
            .catch(function () {
                // Silently ignore fetch errors (server may be restarting)
            });
    }

    function startLogin() {
        window.open("https://play.qobuz.com/login", "_blank");
        showAuthState("login");
        document.getElementById("login-error").style.display = "none";
        document.getElementById("localuser-value").value = "";
    }

    function cancelLogin() {
        showAuthState("disconnected");
    }

    function parseLocalUser(raw) {
        // Parse the localuser LocalStorage value.
        // Expected format: {"id":8998208,"token":"...","email":"...","name":"...",...}
        var s = raw.trim();
        try {
            var obj = JSON.parse(s);
            if (obj && typeof obj === "object" && obj.id && obj.token) {
                return { user_id: String(obj.id), user_auth_token: obj.token };
            }
        } catch (e) {
            // Not valid JSON
        }
        return null;
    }

    function submitToken(event) {
        event.preventDefault();

        var rawValue = document.getElementById("localuser-value").value;
        var errorEl = document.getElementById("login-error");

        var parsed = parseLocalUser(rawValue);
        if (!parsed) {
            errorEl.textContent = 'Could not parse localuser value. Make sure you copied the full value of the "localuser" key.';
            errorEl.style.display = "";
            return;
        }

        var userId = parsed.user_id;
        var authToken = parsed.user_auth_token;

        errorEl.style.display = "none";

        fetch("/api/auth/token", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ user_id: userId, user_auth_token: authToken }),
        })
            .then(function (response) {
                if (!response.ok) {
                    return response.json().then(function (data) {
                        throw new Error(data.error || "Authentication failed");
                    });
                }
                return response.json();
            })
            .then(function (data) {
                document.getElementById("auth-email").textContent = data.email || "";
                showAuthState("connected");
                fetchStatus();
            })
            .catch(function (err) {
                errorEl.textContent = err.message;
                errorEl.style.display = "";
            });
    }

    function logout() {
        fetch("/api/auth/logout", { method: "POST" })
            .then(function () {
                showAuthState("disconnected");
                document.getElementById("speakers-list").innerHTML =
                    '<p class="muted">Waiting for authentication...</p>';
            })
            .catch(function () {
                // Force UI to disconnected even if request fails
                showAuthState("disconnected");
            });
    }

    // Expose functions to global scope for onclick handlers
    window.startLogin = startLogin;
    window.cancelLogin = cancelLogin;
    window.submitToken = submitToken;
    window.logout = logout;

    // Start polling on page load
    fetchStatus();
    pollTimer = setInterval(fetchStatus, 3000);
})();
