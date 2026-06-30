import { lengthToTone, toneToLength } from "./prefMapping";

it("maps length<->tone both ways", () => {
  expect(lengthToTone("short")).toBe("Concise");
  expect(lengthToTone("medium")).toBe("Balanced");
  expect(lengthToTone("long")).toBe("Detailed");
  expect(lengthToTone("")).toBe("Balanced"); // default
  expect(toneToLength("Detailed")).toBe("long");
});
