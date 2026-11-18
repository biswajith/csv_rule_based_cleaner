import { Component, Fragment, type ReactNode } from "react";
import { Button, Content, Flex, Heading, InlineAlert, View } from "@adobe/react-spectrum";

interface Props {
  children: ReactNode;
}
interface BoundaryState {
  error: Error | null;
  /** Bumped by "Start over" so the whole app subtree remounts with fresh state. */
  generation: number;
}

/** Top-level boundary: shows the error and lets the user restart the app with clean state. */
export class ErrorBoundary extends Component<Props, BoundaryState> {
  state: BoundaryState = { error: null, generation: 0 };

  static getDerivedStateFromError(error: Error): Partial<BoundaryState> {
    return { error };
  }

  render() {
    const { error, generation } = this.state;
    if (!error) return <Fragment key={generation}>{this.props.children}</Fragment>;
    return (
      <View padding="size-300">
        <Flex direction="column" gap="size-200" maxWidth="size-6000">
          <InlineAlert variant="negative">
            <Heading>Something went wrong</Heading>
            <Content>
              <div role="alert">{error.message || String(error)}</div>
            </Content>
          </InlineAlert>
          <Flex>
            <Button variant="accent" onPress={() => this.setState((s) => ({ error: null, generation: s.generation + 1 }))}>
              Start over
            </Button>
          </Flex>
        </Flex>
      </View>
    );
  }
}
