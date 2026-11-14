// Picker summaries. The scenario API supplies the incident narrative and model inputs.
export const demoIncidents = [
  {
    id: "disruption",
    title: "Possible crash on CA-47",
    tag: "Uncertain obstruction",
    mission: "Check whether a wreck is blocking the road",
    footageId: "crash-lanes-blocked",
  },
  {
    id: "disruption_b",
    title: "Corroborated CA-47 closure",
    tag: "Stronger evidence",
    mission: "Check whether the corroborated closure is visible",
    footageId: "crash-lanes-blocked",
  },
  {
    id: "congestion",
    title: "Pier 400 exit-road queue",
    tag: "Delay within the buffer",
    mission: "Check queue movement on the outbound access road outside Pier 400",
    footageId: "terminal-truck-queue",
  },
  {
    id: "flood",
    title: "Water on the connector approach",
    tag: "Limited aerial evidence",
    mission: "Inspect visible flooding; safe water depth remains unknown",
    footageId: "flooded-lane",
  },
];
