/// Computes the majority vote for each bit of the supplied 64-bit hashes.
///
/// # Safety
/// For a nonzero length, hashes must point to that many aligned, readable u64s.
#[no_mangle]
pub unsafe extern "C" fn simhash64(hashes: *const u64, length: usize) -> u64 {
    if length == 0 {
        return 0;
    }
    let hashes = unsafe { std::slice::from_raw_parts(hashes, length) };
    let mut votes = [0usize; 64];
    for &hash in hashes {
        for (bit, count) in votes.iter_mut().enumerate() {
            *count += ((hash >> bit) & 1) as usize;
        }
    }

    let mut fingerprint = 0;
    for (bit, &count) in votes.iter().enumerate() {
        if count > length / 2 {
            fingerprint |= 1u64 << bit;
        }
    }
    fingerprint
}
