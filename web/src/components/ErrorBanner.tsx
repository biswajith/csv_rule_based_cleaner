import { Content, Heading, InlineAlert, View } from "@adobe/react-spectrum";
import { ApiError } from "../apiError";

export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, e instanceof Error ? e.message : String(e));
}

export function ErrorBanner({ error, title = "Something went wrong" }: { error: ApiError | null; title?: string }) {
  if (!error) return null;
  return (
    <InlineAlert variant="negative" width="100%">
      <Heading>{title}</Heading>
      <Content>
        <div role="alert">
          {error.detail}
          {error.errors && error.errors.length > 0 && (
            <View marginTop="size-100">
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {error.errors.map((m, i) => (
                  <li key={i}>{m}</li>
                ))}
              </ul>
            </View>
          )}
        </div>
      </Content>
    </InlineAlert>
  );
}

export const RESTART_NOTICE =
  "The server no longer knows this source or run (it was probably restarted). Please connect again.";
