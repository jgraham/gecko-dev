/* vim: set ts=2 et sw=2 tw=80: */
/* Any copyright is dedicated to the Public Domain.
 http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

// Test that the box-model values are editable and that keybindings work

let test = asyncTest(function*() {
  let style = "div { margin: 10px; padding: 3px } #div1 { margin-top: 5px } #div2 { border-bottom: 1em solid black; } #div3 { padding: 2em; }";
  let html = "<style>" + style + "</style><div id='div1'></div><div id='div2'></div><div id='div3'></div>"
  yield addTab("data:text/html," + encodeURIComponent(html));

  let {inspector, view} = yield openLayoutView();
  let viewDoc = view.doc;
  let viewWin = viewDoc.defaultView;

  yield testEditMarginAndCancel(inspector, viewDoc, viewWin);
  yield testArrowKeyAndEnterToCommit(inspector, viewDoc, viewWin);
  yield testDeleteValueAndUndoWithEscape(inspector, viewDoc, viewWin);
  yield testDeleteValue(inspector, viewDoc, viewWin);
});

function* testEditMarginAndCancel(inspector, viewDoc, viewWin) {
  info("Test that editing margin dynamically updates the document, pressing escape cancels the changes");

  let node = getNode("#div1");
  is(getStyle(node, "margin-top"), "", "Should be no margin-top on the element.")
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".margin.top > span");
  is(span.textContent, 5, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "5px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("3", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "margin-top"), "3px", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_ESCAPE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "margin-top"), "", "Should be no margin-top on the element.")
  is(span.textContent, 5, "Should have the right value in the box model.");
}

function* testArrowKeyAndEnterToCommit(inspector, viewDoc, viewWin) {
  info("Test that arrow keys work correctly and pressing enter commits the changes");

  let node = getNode("#div1");
  is(getStyle(node, "margin-left"), "", "Should be no margin-top on the element.")
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".margin.left > span");
  is(span.textContent, 10, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "10px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_UP", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "11px", "Should have the right value in the editor.");
  is(getStyle(node, "margin-left"), "11px", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_DOWN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "10px", "Should have the right value in the editor.");
  is(getStyle(node, "margin-left"), "10px", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_UP", { shiftKey: true }, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "20px", "Should have the right value in the editor.");
  is(getStyle(node, "margin-left"), "20px", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "margin-left"), "20px", "Should be the right margin-top on the element.")
  is(span.textContent, 20, "Should have the right value in the box model.");
}

function* testDeleteValueAndUndoWithEscape(inspector, viewDoc, viewWin) {
  info("Test that deleting the value removes the property but escape undoes that");

  let node = getNode("#div1");
  is(getStyle(node, "margin-left"), "20px", "Should be the right margin-top on the element.")
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".margin.left > span");
  is(span.textContent, 20, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "20px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_DELETE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "", "Should have the right value in the editor.");
  is(getStyle(node, "margin-left"), "", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_ESCAPE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "margin-left"), "20px", "Should be the right margin-top on the element.")
  is(span.textContent, 20, "Should have the right value in the box model.");
}

function* testDeleteValue(inspector, viewDoc, viewWin) {
  info("Test that deleting the value removes the property");

  let node = getNode("#div1");
  node.style.marginRight = "15px";
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".margin.right > span");
  is(span.textContent, 15, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "15px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_DELETE", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "", "Should have the right value in the editor.");
  is(getStyle(node, "margin-right"), "", "Should have updated the margin.");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "margin-right"), "", "Should be the right margin-top on the element.")
  is(span.textContent, 10, "Should have the right value in the box model.");
}
