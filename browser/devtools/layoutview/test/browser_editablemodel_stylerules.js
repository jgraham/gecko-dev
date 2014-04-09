/* vim: set ts=2 et sw=2 tw=80: */
/* Any copyright is dedicated to the Public Domain.
 http://creativecommons.org/publicdomain/zero/1.0/ */

"use strict";

// Test that the box model is editable and that units do work and that values
// are retrieved correctly

let test = asyncTest(function*() {
  let style = "div { margin: 10px; padding: 3px } #div1 { margin-top: 5px } #div2 { border-bottom: 1em solid black; } #div3 { padding: 2em; }";
  let html = "<style>" + style + "</style><div id='div1'></div><div id='div2'></div><div id='div3'></div>"

  yield addTab("data:text/html," + encodeURIComponent(html));
  let {inspector, view} = yield openLayoutView();

  let viewDoc = view.doc;
  let viewWin = viewDoc.defaultView;

  yield testUnits(inspector, viewDoc, viewWin);
  yield testHigherStyleRule(inspector, viewDoc, viewWin);
  yield testShorthandProperties(inspector, viewDoc, viewWin);
});

function* testUnits(inspector, viewDoc, viewWin) {
  info("Test that entering units works");

  let node = getNode("#div1");
  is(getStyle(node, "padding-top"), "", "Should have the right padding");
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.top > span");
  is(span.textContent, 3, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "3px", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("1", {}, viewWin);
  yield waitForUpdate(inspector);
  EventUtils.synthesizeKey("e", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-top"), "", "An invalid value is handled cleanly");

  EventUtils.synthesizeKey("m", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "1em", "Should have the right value in the editor.");
  is(getStyle(node, "padding-top"), "1em", "Should have updated the padding.");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-top"), "1em", "Should be the right padding.")
  is(span.textContent, 16, "Should have the right value in the box model.");
}

function* testHigherStyleRule(inspector, viewDoc, viewWin) {
  info("Test that we pick up the value from a higher style rule");

  let node = getNode("#div2");
  is(getStyle(node, "border-bottom-width"), "", "Should have the right border-bottom-width");
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".border.bottom > span");
  is(span.textContent, 16, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "1em", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("0", {}, viewWin);
  yield waitForUpdate(inspector);

  is(editor.value, "0", "Should have the right value in the editor.");
  is(getStyle(node, "border-bottom-width"), "0px", "Should have updated the border.");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "border-bottom-width"), "0px", "Should be the right border-bottom-width.")
  is(span.textContent, 0, "Should have the right value in the box model.");
}

function* testShorthandProperties(inspector, viewDoc, viewWin) {
  info("Test that shorthand properties are parsed correctly");

  let node = getNode("#div3");
  is(getStyle(node, "padding-right"), "", "Should have the right padding");
  yield selectNode(node, inspector);

  let span = viewDoc.querySelector(".padding.right > span");
  is(span.textContent, 32, "Should have the right value in the box model.");

  EventUtils.synthesizeMouseAtCenter(span, {}, viewWin);
  let editor = viewDoc.querySelector(".styleinspector-propertyeditor");
  ok(editor, "Should have opened the editor.");
  is(editor.value, "2em", "Should have the right value in the editor.");

  EventUtils.synthesizeKey("VK_RETURN", {}, viewWin);
  yield waitForUpdate(inspector);

  is(getStyle(node, "padding-right"), "", "Should be the right padding.")
  is(span.textContent, 32, "Should have the right value in the box model.");
}
