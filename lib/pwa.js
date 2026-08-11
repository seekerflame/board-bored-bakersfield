/* pwa.js — Board Bored offline layer.
 *
 * THE POINT: a sidewalk event has terrible cell service. Someone fills in the
 * feedback form, hits send, and the POST fails. Without this, their words are
 * gone and they walk away thinking it worked. With this, the submission is
 * saved on their device and sent the moment signal returns — they never know
 * anything went wrong.
 *
 * Three parts:
 *   1. a storage-agnostic queue (the core logic, unit-tested in Node)
 *   2. an IndexedDB implementation of that store (the real device storage)
 *   3. service-worker registration + install prompt
 *
 * SUBPATH NOTE: this ships on GitHub project pages under /board-bored-bakersfield/,
 * so nothing may assume it lives at the domain root. The service-worker URL is
 * derived from THIS script's own location, which is correct locally and deployed.
 */
(function (global) {
  "use strict";

  var THIS_SRC = (document.currentScript && document.currentScript.src) || "";
  // .../board-bored/lib/pwa.js  ->  .../board-bored/
  var BASE = THIS_SRC.replace(/lib\/pwa\.js(\?.*)?$/, "");

  // ---------------------------------------------------------------- queue core
  // A `store` is: add(item)->Promise<key>, all()->Promise<[{key,item}]>,
  //               del(key)->Promise, put(key,item)->Promise
  // Kept free of IndexedDB so the retry/drop logic can be tested with a plain Map.
  var MAX_TRIES = 10;

  function makeQueue(store) {
    return {
      enqueue: function (endpoint, payload) {
        return store.add({ endpoint: endpoint, payload: payload, tries: 0 });
      },
      pending: function () {
        return store.all().then(function (x) { return x.length; });
      },
      // doFetch(endpoint, payload) -> Promise<{ok:bool}>; REJECTS on network failure.
      flush: function (doFetch) {
        return store.all().then(function (items) {
          var acc = { sent: 0, kept: 0, dropped: 0 };
          var chain = Promise.resolve();
          items.forEach(function (rec) {
            chain = chain.then(function () {
              return doFetch(rec.item.endpoint, rec.item.payload).then(
                function (res) {
                  if (res && res.ok) {
                    return store.del(rec.key).then(function () { acc.sent++; });
                  }
                  // Server answered but rejected the payload (4xx). Retrying won't
                  // fix bad data; count a try and drop once it's clearly poison.
                  rec.item.tries++;
                  if (rec.item.tries >= MAX_TRIES) {
                    return store.del(rec.key).then(function () { acc.dropped++; });
                  }
                  return store.put(rec.key, rec.item).then(function () { acc.kept++; });
                },
                function () {
                  // Network failure — still offline. Keep it, no penalty.
                  acc.kept++;
                }
              );
            });
          });
          return chain.then(function () { return acc; });
        });
      },
    };
  }

  // ---------------------------------------------------------------- IndexedDB store
  var DB_NAME = "bb-queue", STORE = "submissions", DB_VERSION = 1;

  function idbOpen() {
    return new Promise(function (res, rej) {
      var r = global.indexedDB.open(DB_NAME, DB_VERSION);
      r.onupgradeneeded = function () {
        r.result.createObjectStore(STORE, { keyPath: "key", autoIncrement: true });
      };
      r.onsuccess = function () { res(r.result); };
      r.onerror = function () { rej(r.error); };
    });
  }

  function idbTx(mode, fn) {
    return idbOpen().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(STORE, mode);
        var os = tx.objectStore(STORE);
        var out = fn(os);
        tx.oncomplete = function () { res(out.value); };
        tx.onerror = function () { rej(tx.error); };
      });
    });
  }

  var idbStore = {
    add: function (item) {
      var box = {};
      return idbTx("readwrite", function (os) {
        var req = os.add(item);
        req.onsuccess = function () { box.value = req.result; };
        return box;
      });
    },
    all: function () {
      var box = {};
      return idbTx("readonly", function (os) {
        var req = os.getAll();
        req.onsuccess = function () {
          box.value = (req.result || []).map(function (r) {
            return { key: r.key, item: r };
          });
        };
        return box;
      });
    },
    del: function (key) {
      return idbTx("readwrite", function (os) { os.delete(key); return {}; });
    },
    put: function (key, item) {
      var rec = {};
      for (var k in item) { if (item.hasOwnProperty(k)) rec[k] = item[k]; }
      rec.key = key;
      return idbTx("readwrite", function (os) { os.put(rec); return {}; });
    },
  };

  // ---------------------------------------------------------------- submit wrapper
  function doFetch(endpoint, payload) {
    return fetch(endpoint, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) { return { ok: r.ok, status: r.status }; });
  }

  var haveIDB = ("indexedDB" in global);
  var queue = haveIDB ? makeQueue(idbStore) : null;

  // The public call the pages use instead of raw fetch().
  // Resolves { ok, queued }. queued:true means "saved locally, offline".
  function bbSubmit(endpoint, payload) {
    return doFetch(endpoint, payload).then(
      function (res) { return { ok: res.ok, queued: false, status: res.status }; },
      function () {
        if (!queue) return { ok: false, queued: false, offline: true };
        return queue.enqueue(endpoint, payload).then(function () {
          return { ok: true, queued: true };
        });
      }
    );
  }

  function bbFlush() {
    if (!queue) return Promise.resolve(null);
    return queue.flush(doFetch);
  }

  function bbPending() {
    if (!queue) return Promise.resolve(0);
    return queue.pending();
  }

  // flush whenever we (re)connect or the page loads
  if (haveIDB) {
    global.addEventListener("online", bbFlush);
    global.addEventListener("load", bbFlush);
  }

  // ---------------------------------------------------------------- service worker
  function registerSW() {
    if (!("serviceWorker" in navigator) || !BASE) return;
    navigator.serviceWorker
      .register(BASE + "sw.js", { scope: BASE })
      .catch(function () { /* offline-first is a bonus, never a hard failure */ });
  }
  if (document.readyState === "complete") registerSW();
  else global.addEventListener("load", registerSW);

  // ---------------------------------------------------------------- install prompt
  var deferred = null;
  global.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    deferred = e;
    if (global.BB_onInstallable) global.BB_onInstallable();
  });
  function bbInstall() {
    if (!deferred) return Promise.resolve(false);
    deferred.prompt();
    return deferred.userChoice.then(function (c) {
      deferred = null;
      return c && c.outcome === "accepted";
    });
  }

  // ---------------------------------------------------------------- exports
  global.BB = {
    submit: bbSubmit,
    flush: bbFlush,
    pending: bbPending,
    install: bbInstall,
    canInstall: function () { return !!deferred; },
    _makeQueue: makeQueue, // exported for the Node test harness
    _base: BASE,
  };
})(typeof self !== "undefined" ? self : this);
