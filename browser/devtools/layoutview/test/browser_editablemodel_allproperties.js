/* vim: set ts=2 et sw=2 tw=80: */
/* Any copyright is dedicated to the Public Domain.
 http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

// Test that the box-model values are editable when all values are defined

let test = asyncTest(function*() {
  let style = "div { margin: 10px; padding: 3px } #div1 { margin-top: 5px } #div2 { border-bottom: 1em solid black; } #div3 { padding: 2em; }";
  let html = "<style>" + style + "</style><div id='div1'></div><div id='div2'></div><div id='div3'></div>"

  yield addTab("data:text/html," + encodeURIComponent(html));

  let {inspector, view} = yield openLayoutView();
  let viewDoc = view.doc;
  let viewWin = viewDoc.defaultView;

  yield testEdit(inspector, viewDoc, viewWin);
  yield testEditAndCancel(inspector, viewDoc, viewWin);
  yield testDelete(inspector, viewDoc, viewWin);
  yield testDeleteAndCancel(inspector, viewDoc, viewWin);
});

function* testEdit(inspector, viewDoc, viewWin) {
  info("When all properties are set on the node editing one should work");

  let node = getNode("#div1");
  node.style.padding = "5px";
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.bottom > span");
  is(span.textContent, 5, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "5px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("7", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "7", "Should have the right value in the editor.");
  is(getStyle(node, "padding-bottom"), "7px", "Should have updated the padding");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-bottom"), "7px", "Should be the right padding.")
  is(span.textContent, 7, "Should have the right value in the box model.");
}

function* testEditAndCancel(inspector, viewDoc, viewWin) {
  info("When all properties are set on the node editing one should work");

  let node = getNode("#div1");
  node.style.padding = "5px";
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.left > span");
  is(span.textContent, 5, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "5px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("8", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "8", "Should have the right value in the editor.");
  is(getStyle(node, "padding-left"), "8px", "Should have updated the padding");

  EventUtils.synthesizeKey("VK_ESCAPE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-left"), "5px", "Should be the right padding.")
  is(span.textContent, 5, "Should have the right value in the box model.");
}

function* testDelete(inspector, viewDoc, viewWin) {
  info("When all properties are set on the node deleting one should work");

  let node = getNode("#div1");
  node.style.padding = "5px";
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.left > span");
  is(span.textContent, 5, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "5px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_DELETE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "", "Should have the right value in the editor.");
  is(getStyle(node, "padding-left"), "", "Should have updated the padding");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-left"), "", "Should be the right padding.")
  is(span.textContent, 3, "Should have the right value in the box model.");
}

function* testDeleteAndCancel(inspector, viewDoc, viewWin) {
  info("When all properties are set on the node deleting one then cancelling should work");

  let node = getNode("#div1");
  node.style.padding = "5px";
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.left > span");
  is(span.textContent, 5, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "5px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_DELETE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "", "Should have the right value in the editor.");
  is(getStyle(node, "padding-left"), "", "Should have updated the padding");

  EventUtils.synthesizeKey("VK_ESCAPE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-left"), "5px", "Should be the right padding.")
  is(span.textContent, 5, "Should have the right value in the box model.");
}
