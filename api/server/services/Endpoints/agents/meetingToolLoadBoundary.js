/**
 * Keep meeting policy authorization directly before loading tools.
 * @template T
 * @param {() => Promise<void>} authorize
 * @param {() => Promise<T>} loadTools
 * @returns {Promise<T>}
 */
async function loadToolsAfterMeetingAuthorization(authorize, loadTools) {
  await authorize();
  return loadTools();
}

module.exports = { loadToolsAfterMeetingAuthorization };
