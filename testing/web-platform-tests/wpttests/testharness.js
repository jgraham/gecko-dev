/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

window.wrappedJSObject.timeout_multiplier = 1;

function listener(e) {
    if(e.data.type == "complete") {
        clearTimeout(timer);
        removeEventListener("message", listener);
        var test_results = e.data.tests.map(function(x) {
            return {name:x.name, status:x.status, message:x.message}
        });
        marionetteScriptFinished({test:"%(url)s",
                                  tests:test_results,
                                  status: e.data.status.status,
                                  message: e.data.status.message});
    }
}
addEventListener("message", listener, false);

// Using an iframe is a hack for the moment
iframe = document.createElement("iframe");
document.body.appendChild(iframe);
iframe.src = "%(abs_url)s";

iframe.onload = function() {
window.wrappedJSObject.win = iframe.contentWindow;
}

//window.open("%(abs_url)s", "%(window_id)s");

var timer = setTimeout(function() {
    window.wrappedJSObject.win.timeout();
}, %(timeout)s);
