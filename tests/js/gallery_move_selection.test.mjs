import test from "node:test";
import assert from "node:assert/strict";
import { MoveSelection } from "../../pages/gallery/move_selection.mjs";

test("single click toggles and preserves sorted numbered selection", () => {
  const s = new MoveSelection();
  s.reset("图片");
  assert.deepEqual(s.toggle("15.png"), { ok: true });
  assert.deepEqual(s.toggle("12.png"), { ok: true });
  assert.deepEqual(s.names(), ["12.png", "15.png"]);
  assert.equal(s.has("15.png"), true);
  s.toggle("15.png");
  assert.deepEqual(s.names(), ["12.png"]);
});

test("pagination and category switching are predictable", () => {
  const s = new MoveSelection();
  s.reset("图片");
  s.addVisible(["11.png", "12.png"]);
  s.addVisible(["12.png", "13.png"]);
  assert.equal(s.count, 3);
  s.reset("airi图片");
  assert.equal(s.count, 0);
  assert.deepEqual(s.names(), []);
});

test("50-file limit blocks overflow and ignores invalid image names", () => {
  const s = new MoveSelection();
  s.reset("图片");
  const images = Array.from({ length: 80 }, (_, i) => String(i + 1) + ".png");
  assert.equal(s.addVisible(images), 50);
  assert.equal(s.count, 50);
  assert.equal(s.toggle("51.png").ok, false);
  assert.equal(s.toggle("../12.png").ok, false);
  s.toggle("1.png");
  assert.equal(s.count, 49);
  assert.equal(s.toggle("51.png").ok, true);
  assert.equal(s.count, 50);
});

test("selection does not include unnumbered files", () => {
  const s = new MoveSelection();
  assert.equal(s.toggle("test.png").ok, false);
  assert.equal(s.addVisible(["banner.webp", "12.png", "0.png"]), 1);
  assert.deepEqual(s.names(), ["12.png"]);
});
