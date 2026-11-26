// The name each photo is uploaded under, which the server returns unchanged as
// its photo_id (SPECS §6.1). The results screen finds a photo's preview by that
// photo_id, so every upload needs a distinct, non-empty name: two picks with the
// same filename would otherwise share one preview slot.
//
// Names are kept exactly as picked (case and extension included). A missing
// name becomes "image.jpg"; a repeat gets "_N" before its extension, using the
// lowest N not already taken. This matches api/main.py:_photo_ids, so the
// server never needs to rename anything the PWA sends.

export function uploadNames(names) {
  const used = new Set();
  return names.map((raw) => {
    let name = raw || "image.jpg";
    if (used.has(name)) {
      const dot = name.lastIndexOf(".");
      const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
      let n = 1;
      while (used.has(`${stem}_${n}${ext}`)) n += 1;
      name = `${stem}_${n}${ext}`;
    }
    used.add(name);
    return name;
  });
}
