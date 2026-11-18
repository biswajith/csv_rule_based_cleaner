export class ApiError extends Error {
  status: number;
  detail: string;
  errors?: string[];
  constructor(status: number, detail: string, errors?: string[]) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.errors = errors;
  }
}

