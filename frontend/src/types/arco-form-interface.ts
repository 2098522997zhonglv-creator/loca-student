/** 替代 form/interface，放宽 FieldRule。 */
export type ValidateStatus = string;
export type ValidateTrigger = string;
export type FieldRule<FieldValue = any> = Record<string, any>;
export type FieldData = Record<string, any>;
export type ValidatedError = Record<string, any>;
export type FormItemEventHandler = (...args: any[]) => any;
export const VALIDATE_STATUSES = ['success', 'warning', 'error', 'validating'] as const;
export const VALIDATE_TRIGGERS = ['change', 'input', 'focus', 'blur'] as const;
