/* Hofradar front-end.
 *
 * Three small jobs, no framework:
 *   1. Make the two headline sliders feel immediate - the readouts and the
 *      derived bands update while dragging, before the server answers.
 *   2. Keep the address bar in sync with the sliders, so a search is a link.
 *   3. Draw the map, and admit it when the tiles do not arrive.
 */
(function () {
  "use strict";

  /* Every URL this file builds is root-absolute, which is correct when the app
   * is served from the root and wrong when a static export of it is served
   * from a subdirectory (GitHub Pages project sites). The exporter sets
   * window.HOFRADAR_BASE; unset, this is "" and nothing changes. */
  var base = window.HOFRADAR_BASE || "";

  var deFormat = function (value, decimals) {
    try {
      return new Intl.NumberFormat("de-DE", {
        minimumFractionDigits: decimals || 0,
        maximumFractionDigits: decimals || 0,
      }).format(value);
    } catch (err) {
      return String(Math.round(value));
    }
  };

  var eur = function (value) { return deFormat(value, 0) + " €"; };
  var km = function (value) { return deFormat(value, 1) + " km"; };
  var roundTo = function (value, places) {
    var factor = Math.pow(10, places);
    return Math.round(value * factor) / factor;
  };

  /* -------- 1. live slider readouts and derived bands -------- */

  function updateDerived(form) {
    if (!form) return;
    var air = parseFloat(form.querySelector("#air_km_max") ? form.querySelector("#air_km_max").value : "NaN");
    var budget = parseFloat(form.querySelector("#total_budget_max") ? form.querySelector("#total_budget_max").value : "NaN");
    var data = form.dataset;

    var set = function (id, text) {
      var node = document.getElementById(id);
      if (node) node.textContent = text;
    };

    if (!isNaN(air)) {
      set("out-air", deFormat(air, 0) + " km");
      set("out-drive-soft", km(roundTo(air * parseFloat(data.drivingSoft || "1.25"), 1)));
      set("out-drive-hard", km(roundTo(air * parseFloat(data.drivingHard || "1.45"), 1)));
    }
    if (!isNaN(budget)) {
      // Mirrors BudgetConfig.effective_* exactly, including the round(-3).
      var target = Math.round((budget * parseFloat(data.purchaseShare || "0.625")) / 1000) * 1000;
      var negotiation = Math.round((target * parseFloat(data.negotiationUplift || "1.133")) / 1000) * 1000;
      var hard = Math.round((target * parseFloat(data.exceptionalUplift || "1.2")) / 1000) * 1000;
      set("out-budget", eur(budget));
      set("out-purchase-target", eur(target));
      set("out-purchase-negotiation", eur(negotiation));
      set("out-purchase-hard", eur(hard));
    }
  }

  function bindControls() {
    var form = document.getElementById("controls");
    if (!form) return;
    updateDerived(form);
    form.addEventListener("input", function () { updateDerived(form); });
  }

  /* -------- 2. the address bar follows the sliders -------- */

  function syncUrl(form) {
    if (!form || !window.history || !window.history.replaceState) return;
    var params = new URLSearchParams(new FormData(form)).toString();
    var target = base + "/";
    window.history.replaceState({}, "", target + (params ? "?" + params : ""));
    var links = document.querySelectorAll(".controls__links a");
    for (var i = 0; i < links.length; i++) {
      var href = links[i].getAttribute("href").split("?")[0];
      links[i].setAttribute("href", href + "?" + params);
    }
  }

  document.body.addEventListener("htmx:afterRequest", function (event) {
    var form = document.getElementById("controls");
    if (form && event.target && (event.target === form || form.contains(event.target))) {
      syncUrl(form);
    }
  });

  /* -------- copy markdown -------- */

  function bindCopy() {
    var button = document.getElementById("copy-markdown");
    if (!button) return;
    button.addEventListener("click", function () {
      var source = document.getElementById(button.dataset.target);
      var feedback = document.getElementById("copy-feedback");
      if (!source) return;
      var done = function (ok) {
        if (feedback) feedback.textContent = ok ? " Kopiert." : " Kopieren nicht möglich – Text markieren.";
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(source.value).then(function () { done(true); }, function () { done(false); });
      } else {
        source.removeAttribute("hidden");
        source.select();
        done(document.execCommand && document.execCommand("copy"));
      }
    });
  }

  /* -------- 3. the map -------- */

  function bindMap() {
    var node = document.getElementById("map");
    if (!node) return;
    var fallback = document.getElementById("map-fallback");
    if (typeof L === "undefined") {
      if (fallback) fallback.hidden = false;
      node.style.display = "none";
      return;
    }

    var points = [];
    try { points = JSON.parse(node.dataset.points || "[]"); } catch (err) { points = []; }

    var lat = parseFloat(node.dataset.centerLat);
    var lon = parseFloat(node.dataset.centerLon);
    var map = L.map(node).setView([lat, lon], 9);

    var tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap",
    });
    // Tiles are the one thing that is not vendored. If they fail, say so.
    var tileFailures = 0;
    tiles.on("tileerror", function () {
      tileFailures += 1;
      if (tileFailures === 3 && fallback) fallback.hidden = false;
    });
    tiles.addTo(map);

    var radiusKm = parseFloat(node.dataset.radiusKm || "0");
    if (radiusKm > 0) {
      L.circle([lat, lon], {
        radius: radiusKm * 1000,
        color: "#4a6d3a", weight: 1, fillOpacity: 0.05,
      }).addTo(map);
    }
    L.circleMarker([lat, lon], {
      radius: 7, color: "#4a6d3a", fillColor: "#4a6d3a", fillOpacity: 1,
    }).addTo(map).bindPopup("Suchzentrum: " + (node.dataset.centerName || ""));

    var colourFor = function (point) {
      var score = point.scores ? point.scores.final : null;
      if (score === null || score === undefined) return "#86837a";
      if (score >= 75) return "#2c6a4a";
      if (score >= 55) return "#8a8a12";
      if (score >= 35) return "#8a5a12";
      return "#96331f";
    };

    var bounds = [[lat, lon]];
    points.forEach(function (point) {
      var colour = colourFor(point);
      // Hollow marker = we only know the town or the postcode. Never imply more.
      var marker = L.circleMarker([point.lat, point.lon], {
        radius: point.precise ? 8 : 7,
        color: colour,
        weight: 2,
        fillColor: point.precise ? colour : "transparent",
        fillOpacity: point.precise ? 0.9 : 0,
        dashArray: point.precise ? null : "3 3",
      });
      var driving = point.distance_driving_checked
        ? km(point.distance_driving_km)
        : "nicht geprüft";
      marker.bindPopup(
        "<b>" + (point.title || "") + "</b><br>" +
        (point.town || "Ort unbekannt") + "<br>" +
        (point.price === null ? "k. A." : eur(point.price)) + "<br>" +
        "Luftlinie " + (point.distance_air_km === null ? "k. A." : km(point.distance_air_km)) + "<br>" +
        "Fahrstrecke " + driving + "<br>" +
        (point.precise ? "" : "<i>Standort nur " + point.geo_precision + "</i><br>") +
        '<a href="' + base + '/property/' + point.public_id + '">Dossier öffnen</a>'
      );
      marker.addTo(map);
      bounds.push([point.lat, point.lon]);
    });

    if (bounds.length > 1) {
      try { map.fitBounds(bounds, { padding: [30, 30] }); } catch (err) { /* keep default view */ }
    }
  }

  function init() {
    bindControls();
    bindCopy();
    bindMap();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
