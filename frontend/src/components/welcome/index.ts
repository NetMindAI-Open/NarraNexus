/**
 * @file_name: index.ts
 * @author: NetMind.AI
 * @date: 2026-08-27
 * @description: Barrel for the first-run welcome flow's pieces. Only
 * [[WelcomePage]] composes them; nothing else should reach in.
 */

export { WelcomeRail } from './WelcomeRail';
export type { WelcomeRailStep } from './WelcomeRail';
export { WelcomeStepFrame } from './WelcomeStepFrame';
export { StepModel } from './StepModel';
export { StepImport } from './StepImport';
export { StepAgent } from './StepAgent';
